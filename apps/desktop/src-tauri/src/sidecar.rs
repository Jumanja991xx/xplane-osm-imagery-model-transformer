//! Starts the Python backend as a hidden child process and hands port + token
//! to the frontend.
//!
//! Protocol (see backend/src/xpsb/__main__.py):
//! - the token is passed in the XPSB_TOKEN environment variable (not argv, so
//!   it does not show up in process lists)
//! - the backend prints one line `XPSB_READY {"port": ...}` to stdout
//! - `--watch-stdin`: the backend exits when its stdin closes, so it never
//!   outlives the app, even if the app is killed

use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Condvar, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};

const READY_PREFIX: &str = "XPSB_READY ";
const START_TIMEOUT: Duration = Duration::from_secs(90);

#[derive(Clone, Serialize)]
pub struct BackendInfo {
    pub port: u16,
    pub token: String,
}

#[derive(Deserialize)]
struct ReadyLine {
    port: u16,
}

enum Status {
    Starting,
    Ready(BackendInfo),
    Failed(String),
}

pub struct Backend {
    status: Mutex<Status>,
    changed: Condvar,
    child: Mutex<Option<Child>>,
}

impl Default for Backend {
    fn default() -> Self {
        Self { status: Mutex::new(Status::Starting), changed: Condvar::new(), child: Mutex::new(None) }
    }
}

impl Backend {
    pub fn start(self: &Arc<Self>, resource_dir: Option<PathBuf>) {
        if let Err(err) = self.spawn(resource_dir) {
            self.set(Status::Failed(err));
        }
    }

    fn set(&self, status: Status) {
        *self.status.lock().unwrap() = status;
        self.changed.notify_all();
    }

    fn spawn(self: &Arc<Self>, resource_dir: Option<PathBuf>) -> Result<(), String> {
        let token = random_token()?;
        let mut cmd = backend_command(resource_dir.as_deref())?;
        cmd.args(["--port", "0", "--watch-stdin"])
            .env("XPSB_TOKEN", &token)
            .stdin(Stdio::piped()) // kept open; closing it stops the backend
            .stdout(Stdio::piped())
            .stderr(if cfg!(debug_assertions) { Stdio::inherit() } else { Stdio::null() });
        if let Some(dir) = &resource_dir {
            cmd.arg("--resources-dir").arg(dir);
        }
        hide_console(&mut cmd);

        let mut child = cmd.spawn().map_err(|e| format!("cannot start backend: {e}"))?;
        let stdout = child.stdout.take().ok_or("backend stdout missing")?;
        *self.child.lock().unwrap() = Some(child);

        let backend = Arc::clone(self);
        thread::spawn(move || {
            for line in BufReader::new(stdout).lines() {
                let Ok(line) = line else { break };
                if let Some(json) = line.strip_prefix(READY_PREFIX) {
                    match serde_json::from_str::<ReadyLine>(json) {
                        Ok(ready) => backend.set(Status::Ready(BackendInfo { port: ready.port, token: token.clone() })),
                        Err(e) => backend.set(Status::Failed(format!("bad ready line: {e}"))),
                    }
                }
            }
            // stdout closed: if we never became ready, the backend died during startup.
            let mut status = backend.status.lock().unwrap();
            if matches!(*status, Status::Starting) {
                *status = Status::Failed("backend exited during startup".into());
                backend.changed.notify_all();
            }
        });
        Ok(())
    }

    pub fn wait_ready(&self) -> Result<BackendInfo, String> {
        let deadline = Instant::now() + START_TIMEOUT;
        let mut status = self.status.lock().unwrap();
        loop {
            match &*status {
                Status::Ready(info) => return Ok(info.clone()),
                Status::Failed(err) => return Err(err.clone()),
                Status::Starting => {
                    let now = Instant::now();
                    if now >= deadline {
                        return Err("backend did not start in time".into());
                    }
                    status = self.changed.wait_timeout(status, deadline - now).unwrap().0;
                }
            }
        }
    }

    /// Returns true if the backend exited by itself after stdin was closed.
    pub fn stop(&self) -> bool {
        let Some(mut child) = self.child.lock().unwrap().take() else { return true };
        drop(child.stdin.take()); // polite: the backend shuts down on stdin EOF
        let deadline = Instant::now() + Duration::from_secs(5);
        while Instant::now() < deadline {
            if let Ok(Some(_)) = child.try_wait() {
                return true;
            }
            thread::sleep(Duration::from_millis(50));
        }
        let _ = child.kill();
        let _ = child.wait();
        false
    }
}

/// Release: the PyInstaller bundle shipped as app resource.
/// Debug: `uv run` in the repository's backend folder, or XPSB_BACKEND_CMD.
fn backend_command(resource_dir: Option<&Path>) -> Result<Command, String> {
    if cfg!(debug_assertions) {
        if let Ok(custom) = std::env::var("XPSB_BACKEND_CMD") {
            return Ok(Command::new(custom));
        }
        let backend_dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../backend");
        let mut cmd = Command::new("uv");
        cmd.arg("run").arg("--project").arg(backend_dir).args(["xpsb-backend", "--dev-cors"]);
        return Ok(cmd);
    }
    let dir = resource_dir.ok_or("resource directory unknown")?;
    let exe = dir.join("backend").join(if cfg!(windows) { "xpsb-backend.exe" } else { "xpsb-backend" });
    if !exe.is_file() {
        return Err(format!("backend not found at {}", exe.display()));
    }
    Ok(Command::new(exe))
}

#[cfg(windows)]
fn hide_console(cmd: &mut Command) {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    cmd.creation_flags(CREATE_NO_WINDOW);
}

#[cfg(not(windows))]
fn hide_console(_cmd: &mut Command) {}

fn random_token() -> Result<String, String> {
    let mut bytes = [0u8; 32];
    getrandom::fill(&mut bytes).map_err(|e| format!("no randomness: {e}"))?;
    Ok(bytes.iter().map(|b| format!("{b:02x}")).collect())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Write};
    use std::net::TcpStream;

    /// Starts the real backend (debug: via `uv run`), talks to it with the
    /// handed-out token and checks that it stops when the app goes away.
    #[test]
    fn starts_serves_and_stops_backend() {
        let data = std::env::temp_dir().join(format!("xpsb-sidecar-test-{}", std::process::id()));
        std::env::set_var("XPSB_DATA_DIR", &data);

        let backend = Arc::new(Backend::default());
        backend.start(None);
        let info = backend.wait_ready().expect("backend ready");

        let mut stream = TcpStream::connect(("127.0.0.1", info.port)).unwrap();
        write!(
            stream,
            "GET /api/health HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer {}\r\nConnection: close\r\n\r\n",
            info.token
        )
        .unwrap();
        let mut response = String::new();
        stream.read_to_string(&mut response).unwrap();
        assert!(response.starts_with("HTTP/1.1 200"), "{response}");
        assert!(response.contains("\"status\":\"ok\""), "{response}");

        assert!(backend.stop(), "backend did not exit on stdin EOF");
        let _ = std::fs::remove_dir_all(data);
    }
}
