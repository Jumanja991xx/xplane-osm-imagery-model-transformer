"""Default build scripts per category (Starlark). They use only the item's
parameters; the AI architect (next step) writes individual scripts in the
same language with the same tools.

Inputs available to every script:
  footprint  polygon [[x, y], ...] in local metres (None for points/lines)
  path       polyline for line objects (None otherwise)
  params     dict: height, levels, roof_shape, roof_color, wall_color, seat_color,
             covered, roof_material, material, name, facing ([x, y] unit
             vector towards the field/track, or None), flip (turn the front
             around), rows (grandstand rows or None), wall_material, building
             (OSM building type, e.g. "industrial", "house", "stadium"), tags (OSM
             tags of the object), detail ("low"|"medium"|"high"), min_height
             (raised blocks), parts (OSM building parts: list of dicts with
             footprint, height, min_height, roof_shape, wall_color, roof_color, part)
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
HALL = ["industrial", "warehouse", "hangar", "hall", "sports_hall", "sports_centre",
        "riding_hall", "manufacture", "farm_auxiliary", "barn", "service", "storage",
        "retail_warehouse", "transportation", "factory", "depot", "cowshed", "stable"]
HOUSE = ["house", "detached", "semidetached_house", "terrace", "bungalow", "residential",
         "farm", "villa"]
SMALL = ["shed", "hut", "kiosk", "toilets", "cabin", "garage", "garages", "carport",
         "container", "allotment_house"]
OFFICE = ["office", "commercial", "retail", "supermarket", "hotel"]
CHURCH = ["church", "chapel", "cathedral", "mosque", "temple", "synagogue", "monastery"]
HISTORIC = ["palace", "castle", "government", "civic", "public", "museum", "townhall",
            "courthouse", "manor", "train_station"]

def style():
    t = params.get("building") or "yes"
    tags = params.get("tags") or {}
    area = polygon_area(footprint)
    if t == "stadium":
        return "stadium"
    if t in HISTORIC or ((tags.get("historic") or tags.get("heritage")) and area > 300
                         and t not in CHURCH and t not in HOUSE and t not in SMALL):
        return "historic"
    if t in HALL or (t == "yes" and area > 1800 and params.get("levels") == None):
        return "hall"
    if t in SMALL or area < 35:
        return "small"
    if t in HOUSE:
        return "house"
    if t in CHURCH:
        return "church"
    if t == "apartments":
        return "apartments"
    if t in OFFICE:
        return "office"
    return "generic"

# style: (height, storey height, wall material, wall colour, roof shape, pitched roof
#         material, flat roof material, roof rise factor, max rise)
STYLES = {
    "hall": (8.0, 4.5, "metal_sheet", "#C8CBCF", "flat", "roof_metal", "roof_metal", 0.15, 4.0),
    "small": (3.0, 2.8, "wood", None, "gabled", "roof_tiles", "roof_flat", 0.35, 2.5),
    "house": (6.5, 2.8, "windows_house", "#E6DCC8", "gabled", "roof_tiles", "roof_flat", 0.45,
              7.0),
    "historic": (16.0, 4.8, "windows_classic", "#DDD4C0", "hipped", "roof_metal", "roof_flat",
                 0.25, 5.0),
    "apartments": (12.0, 3.0, "windows", "#E2DED6", "flat", "roof_tiles", "roof_flat", 0.35, 6.0),
    "office": (10.0, 3.5, "windows", "#D8D8D8", "flat", "roof_metal", "roof_flat", 0.3, 5.0),
    "church": (12.0, 4.0, "plaster", "#E9E2D3", "gabled", "roof_tiles", "roof_flat", 0.7, 14.0),
    "generic": (8.0, 3.0, "windows", None, "flat", "roof_tiles", "roof_flat", 0.35, 6.0),
}

def stadium():
    ring = footprint
    if len(ring) > 90:  # very detailed outlines: even points every few metres
        ring = resample(ring + [ring[0]], 5.0)[:-1]
    r = oriented_rect(ring)
    W = r["width"]
    depth = clamp(W * 0.22, 10, min(42, W * 0.3))
    H = g("height", clamp(W * 0.17, 10, 42))
    facade = g("wall_material", "metal_sheet")
    steps = {"low": 6, "medium": 14, "high": 24}.get(params.get("detail"), 14)
    z_top = H - 2.5
    z_bot = 1.5
    x0 = depth * 0.08
    prof = [[0, 0], [0, H], [x0, H], [x0, z_top]]
    mats = [facade, "concrete", "concrete"]
    colors = [g("wall_color", "#DADADA"), None, None]
    for i in range(steps):
        xb = x0 + (depth - x0) * (i + 1) / steps
        za = z_top - (z_top - z_bot) * i / steps
        zb = z_top - (z_top - z_bot) * (i + 1) / steps
        prof.append([xb, za])
        mats.append("seats")
        colors.append(g("seat_color", None))
        prof.append([xb, zb])
        mats.append("steps")
        colors.append(None)
    prof.append([depth, 0])
    mats.append("concrete")
    colors.append(None)
    mats.append("concrete")  # closing edge along the ground
    colors.append(None)
    line = [[p[0], p[1], 0] for p in ring]
    sweep(line, prof, mats=mats, colors=colors, closed_path=True, caps=True)
    # Roof ring over the stands, falling slightly towards the pitch.
    roof_mat = params.get("roof_material") or "membrane"
    reach = depth * 0.8
    sweep(line, [[-1.5, H + 3.0], [reach, H + 1.2], [reach, H + 1.6], [-1.5, H + 3.4]],
          mats=[roof_mat, "steel", roof_mat, "steel"],  # underside, inner edge, top, outer edge
          colors=[g("roof_color", None), None, g("roof_color", None), None],
          closed_path=True, caps=True)

def block(fp, z0, H, shape, look, flip):
    # One building block: walls from z0 to H and a roof (gabled/hipped/pyramidal/flat).
    wall, wall_mat, gable_mat, roof_color, pitched, flat, rise_k, rise_max = look
    if H <= z0 + 0.2:
        return
    raised = z0 > 0.3
    r = oriented_rect(fp)
    if flip:
        # ridge across instead of along the long side
        r = {"x": r["x"], "y": r["y"], "length": r["width"], "width": r["length"],
             "ux": r["vx"], "uy": r["vy"], "vx": -r["vy"], "vy": r["vx"]}
    rect_like = polygon_area(fp) > 0.85 * r["length"] * r["width"]
    if shape in ("gabled", "hipped") and rect_like:
        extrude(fp, z0, H, mat=wall_mat, top=None, color=wall, bottom=raised)
        P = frame(r)
        L2 = r["length"] / 2
        W2 = r["width"] / 2
        rise = min(r["width"] * rise_k, rise_max)
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
    elif shape in ("pyramidal", "dome", "onion", "cone") and len(fp) <= 40:
        # towers and domes: a pointed roof over the centre
        extrude(fp, z0, H, mat=wall_mat, top=None, color=wall, bottom=raised)
        c = polygon_centroid(fp)
        rise = min(min(r["width"], r["length"]) * (0.9 if shape == "cone" else 0.6), 25)
        apex = [c[0], c[1], H + rise]
        n = len(fp)
        for i in range(n):
            a = fp[i]
            b = fp[(i + 1) % n]
            face([[a[0], a[1], H], [b[0], b[1], H], apex], mat=pitched, color=roof_color)
    else:
        extrude(fp, z0, H, mat=wall_mat, top=flat, color=wall, top_color=roof_color,
                bottom=raised)

def main():
    st = style()
    if st == "stadium":
        stadium()
        return
    H0, storey, wall_def, wall_col, shape_def, pitched_def, flat_def, rise_k, rise_max = STYLES[st]
    H = params.get("height")
    if H == None:
        lv = params.get("levels")
        H = lv * storey if lv else H0
    wall = g("wall_color", wall_col)
    wall_mat = g("wall_material", wall_def)
    gable_mat = "plaster" if wall_mat in ("windows", "glass", "metal_sheet") else wall_mat
    if st == "hall":
        gable_mat = wall_mat
    roof_color = g("roof_color", None)
    roof_mat = params.get("roof_material")
    look = (wall, wall_mat, gable_mat, roof_color, roof_mat or pitched_def, roof_mat or flat_def,
            rise_k, rise_max)
    parts = params.get("parts") or []
    if parts:
        # OSM building parts (towers, wings, raised roofs): each its own block
        for part in parts:
            ph = part.get("height") or H
            pz = part.get("min_height") or 0
            plook = (part.get("wall_color") or wall, wall_mat, gable_mat,
                     part.get("roof_color") or roof_color, look[4], look[5], rise_k, rise_max)
            block(part["footprint"], pz, ph, part.get("roof_shape") or "flat", plook, False)
        if g("parts_cover", 1.0) >= 0.8:
            return  # the parts are the whole building
    shape = g("roof_shape", shape_def)
    block(footprint, g("min_height", 0), H, shape, look, g("flip", False))

main()
"""
)

BARRIER = (
    COMMON
    + """
HEIGHTS = {"wall": 1.8, "retaining_wall": 1.5, "city_wall": 5.0, "fence": 2.0,
           "guard_rail": 0.8, "hedge": 1.5, "handrail": 1.0, "jersey_barrier": 0.8}

def main():
    tags = params.get("tags") or {}
    kind = tags.get("barrier") or "fence"
    closed = path == None
    pts = footprint if closed else path
    H = g("height", HEIGHTS.get(kind, 1.8))
    line = [[p[0], p[1], 0] for p in pts]
    if kind in ("wall", "retaining_wall", "city_wall", "jersey_barrier"):
        t = 0.6 if kind == "city_wall" else 0.15
        mat = g("wall_material", "brick" if tags.get("material") == "brick" else "concrete")
        sweep(line, [[-t, 0], [-t, H], [t, H], [t, 0]], mat=mat, color=g("wall_color", None),
              closed_path=closed, caps=True)
    elif kind == "hedge":
        sweep(line, [[-0.4, 0], [-0.45, H], [0.45, H], [0.4, 0]], mat="paint",
              color="#4E6E35", closed_path=closed, caps=True)
    elif kind == "guard_rail":
        for p in resample(pts + ([pts[0]] if closed else []), 2.0):
            cylinder(p[0], p[1], 0, 0.06, H, mat="steel", segments=4)
        sweep(line, [[-0.05, H - 0.35], [-0.05, H], [0.05, H], [0.05, H - 0.35]],
              mat="steel", closed_path=closed, caps=True)
    else:  # fences: posts and rails (see-through)
        s = 4 if params.get("detail") == "low" else 6
        posts = resample(pts + ([pts[0]] if closed else []), 2.5)
        for p in posts:
            cylinder(p[0], p[1], 0, 0.04, H, mat="steel", segments=s)
        for z in (0.15, H * 0.5, H - 0.05):
            sweep(line, [[-0.025, z - 0.03], [-0.025, z + 0.03], [0.025, z + 0.03],
                         [0.025, z - 0.03]], mat="steel", closed_path=closed, caps=True)

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
    "barriers": BARRIER,
}
