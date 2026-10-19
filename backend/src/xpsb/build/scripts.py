"""Default build scripts per category (Starlark). They use only the item's
parameters; the AI architect (next step) writes individual scripts in the
same language with the same tools.

Inputs available to every script:
  footprint  polygon [[x, y], ...] in local metres (None for points/lines)
  path       polyline for line objects (None otherwise)
  params     dict: height, levels, roof_shape, roof_color, wall_color, seat_color,
             covered, roof_material, material, name, facing ([x, y] unit
             vector towards the field/track, or None), flip (turn the front
             around), rows (grandstand rows or None), detail ("low"|"medium"|"high")
"""

from __future__ import annotations

COMMON = """
def g(key, default):
    v = params.get(key)
    return default if v == None else v

def segs(n_medium):
    d = params.get("detail")
    if d == "low":
        return max(4, n_medium // 2)
    if d == "high":
        return n_medium * 2
    return n_medium

def frame(r):
    # helpers to place points in the oriented rectangle: a along the long side
    return lambda a, b, z: [r["x"] + r["ux"] * a + r["vx"] * b,
                            r["y"] + r["uy"] * a + r["vy"] * b, z]
"""

GRANDSTAND = (
    COMMON
    + """
def main():
    r = oriented_rect(footprint)
    L = r["length"]
    D = r["width"]
    ax = r["ux"]
    ay = r["uy"]
    # Front = the side towards the field (if known): normally a long side, but
    # near-square stands in curves and corners may face it with a short side.
    nx = -ay
    ny = ax
    f = params.get("facing")
    if f != None:
        d_long = nx * f[0] + ny * f[1]
        d_short = ax * f[0] + ay * f[1]
        if abs(d_short) > abs(d_long) + 0.25 and L < 2.5 * D:
            nx = ax
            ny = ay
            d_long = d_short
            L, D = D, L
        if d_long < 0:
            nx = -nx
            ny = -ny
    if g("flip", False):
        nx = -nx
        ny = -ny
    # Walk along the stand so that "left" (+across in sweep profiles) is the back.
    tx = -ny
    ty = nx
    fx = r["x"] + nx * D / 2
    fy = r["y"] + ny * D / 2
    p0 = [fx - tx * L / 2, fy - ty * L / 2, 0]
    p1 = [fx + tx * L / 2, fy + ty * L / 2, 0]

    covered = g("covered", False) == True or g("roof_material", "") == "solar"
    H = params.get("height")
    front_wall = 1.1
    clearance = 2.6  # head room between the top row and the roof
    aisle = 1.2 if D > 6 else 0.5
    rows = max(2, int((D - aisle) / 0.8))
    if H != None:
        # Never more rows than fit under the given height (min. rise 0.25 m);
        # the remaining depth becomes a flat walkway at the back.
        top = H - clearance if covered else H
        rows = max(2, min(rows, int((top - front_wall) / 0.25)))
    if params.get("rows") != None:
        rows = params["rows"]  # the user's choice; the roof goes up if needed
    row_d = min((D - aisle) / rows, 0.95)
    if H != None and covered:
        tier_top = max(front_wall + rows * 0.25, H - clearance)
    elif H != None:
        tier_top = H
    else:
        tier_top = front_wall + rows * 0.42
    row_h = clamp((tier_top - front_wall) / rows, 0.25, 0.6)
    z_top = front_wall + (rows - 1) * row_h
    # The roof always clears the top row, even if the given height is too low.
    roof_h = max(H, z_top + clearance) if (H != None and covered) else z_top + clearance + 0.4

    # Cross-section: front wall, rows of seats (tread + riser), aisle, back wall.
    prof = [[0, 0], [0, front_wall]]
    mats = ["concrete"]
    colors = [None]
    for i in range(rows):
        z = front_wall + i * row_h
        prof.append([(i + 1) * row_d, z])
        mats.append("seats")
        colors.append(g("seat_color", None))
        if i < rows - 1:
            prof.append([(i + 1) * row_d, z + row_h])
            mats.append("steps")
            colors.append(None)
    z_last = front_wall + (rows - 1) * row_h
    prof.append([D, z_last])
    mats.append("concrete")
    colors.append(None)
    prof.append([D, 0])
    mats.append("metal_sheet" if covered else "concrete")
    colors.append(g("wall_color", None))
    mats.append("concrete")  # closing edge along the ground
    colors.append(None)
    sweep([p0, p1], prof, mats=mats, colors=colors, caps=True)

    if not covered:
        return
    # Roof: shed roof falling towards the field, on columns at the back and front.
    roof_mat = g("roof_material", "roof_metal")
    over = 1.5
    sweep([p0, p1], [[-over, roof_h - 1.0], [D + 0.4, roof_h], [D + 0.4, roof_h + 0.25],
                     [-over, roof_h - 0.75]],
          mats=["steel", "steel", roof_mat, "steel"],
          colors=[None, None, g("roof_color", None), None], caps=True)
    # Rear wall between the top row and the roof.
    sweep([p0, p1], [[D, z_last], [D, roof_h]], mat="metal_sheet",
          color=g("wall_color", None), two_sided=True)
    n = max(1, int(L / 8))
    for i in range(n + 1):
        a = -L / 2 + L * i / n
        for across, height in ((D + 0.2, roof_h), (0.3, roof_h - 0.95)):
            x = fx + tx * a - nx * across
            y = fy + ty * a - ny * across
            cylinder(x, y, 0, 0.14, height, mat="steel", segments=segs(8))

main()
"""
)

BUILDING = (
    COMMON
    + """
def main():
    H = params.get("height")
    if H == None:
        lv = params.get("levels")
        H = lv * 3.0 if lv else 8.0
    wall = g("wall_color", None)
    wall_mat = g("wall_material", "windows")
    gable_mat = "plaster" if wall_mat in ("windows", "glass") else wall_mat
    roof_color = g("roof_color", None)
    roof_mat = params.get("roof_material")
    shape = g("roof_shape", "flat")
    r = oriented_rect(footprint)
    if g("flip", False):
        # ridge across instead of along the long side
        r = {"x": r["x"], "y": r["y"], "length": r["width"], "width": r["length"],
             "ux": r["vx"], "uy": r["vy"], "vx": -r["vy"], "vy": r["vx"]}
    rect_like = polygon_area(footprint) > 0.85 * r["length"] * r["width"]
    if shape in ("gabled", "hipped") and rect_like:
        pitched = roof_mat or "roof_tiles"
        extrude(footprint, 0, H, mat=wall_mat, top=None, color=wall)
        P = frame(r)
        L2 = r["length"] / 2
        W2 = r["width"] / 2
        rise = min(r["width"] * 0.35, 6.0)
        A = P(-L2, -W2, H)
        B = P(L2, -W2, H)
        C = P(L2, W2, H)
        D = P(-L2, W2, H)
        if shape == "hipped" and r["length"] > r["width"] * 1.2:
            R1 = P(-L2 + W2, 0, H + rise)
            R2 = P(L2 - W2, 0, H + rise)
            face([A, D, R1], mat=pitched, color=roof_color)
            face([C, B, R2], mat=pitched, color=roof_color)
        else:
            R1 = P(-L2, 0, H + rise)
            R2 = P(L2, 0, H + rise)
            face([A, D, R1], mat=gable_mat, color=wall)
            face([C, B, R2], mat=gable_mat, color=wall)
        quad(A, B, R2, R1, mat=pitched, color=roof_color)
        quad(C, D, R1, R2, mat=pitched, color=roof_color)
    else:
        extrude(footprint, 0, H, mat=wall_mat, top=roof_mat or "roof_flat", color=wall,
                top_color=roof_color)

main()
"""
)

CANOPY = (
    COMMON
    + """
def main():
    H = g("height", 5.0)
    r = oriented_rect(footprint)
    roof_mat = g("roof_material", "roof_metal")
    extrude(footprint, H - 0.3, H, mat="steel", top=roof_mat, top_color=g("roof_color", None),
            bottom=True)
    P = frame(r)
    L2 = r["length"] / 2 - 0.4
    W2 = r["width"] / 2 - 0.4
    n = max(1, int(r["length"] / 6))
    for i in range(n + 1):
        a = -L2 + 2 * L2 * i / n
        for b in (-W2, W2):
            p = P(a, b, 0)
            cylinder(p[0], p[1], 0, 0.12, H - 0.3, mat="steel", segments=segs(8))

main()
"""
)

MAST = (
    COMMON
    + """
def main():
    H = g("height", 20.0)
    cylinder(0, 0, 0, 0.3, H, mat="steel", segments=segs(10), radius_top=0.18)
    f = params.get("facing")
    heading = atan2(f[0], f[1]) + 90 if f != None else 0
    if g("flip", False):
        heading += 180
    box(0, 0, H - 1.2, 3.0, 0.5, 1.2, heading=heading, mat="lamp")

main()
"""
)

SCAFFOLDING = (
    COMMON
    + """
def main():
    H = g("height", 6.0)
    r = oriented_rect(footprint)
    P = frame(r)
    L2 = r["length"] / 2
    W2 = r["width"] / 2
    nx = max(1, int(r["length"] / 2.5))
    levels = max(1, int(H / 2.0))
    s = segs(6)
    for i in range(nx + 1):
        a = -L2 + 2 * L2 * i / nx
        for b in (-W2, W2):
            tube(P(a, b, 0), P(a, b, H), 0.03, mat="steel", segments=s)
    for k in range(1, levels + 1):
        z = H * k / levels
        for b in (-W2, W2):
            tube(P(-L2, b, z), P(L2, b, z), 0.03, mat="steel", segments=s)
        for i in range(nx + 1):
            a = -L2 + 2 * L2 * i / nx
            tube(P(a, -W2, z), P(a, W2, z), 0.03, mat="steel", segments=s)
    box(r["x"], r["y"], H, r["length"], r["width"], 0.05, heading=r["heading"], mat="wood")

main()
"""
)

DEFAULT_SCRIPTS = {
    "grandstands": GRANDSTAND,
    "buildings": BUILDING,
    "canopies": CANOPY,
    "masts": MAST,
    "scaffolding": SCAFFOLDING,
}
