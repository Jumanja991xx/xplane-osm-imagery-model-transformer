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
  textures   photo textures of the object: name -> {width, height, repeat, walls};
             used as mat="photo:<name>"
             (params["street_photos"]: street-level photos will be painted over the
             walls they show - build those walls flat, without modelled windows)
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

def photo_walls():
    # A photo texture the user (or the AI) marked for all walls, else None.
    for name in sorted(textures):
        if textures[name].get("walls"):
            return "photo:" + name
    return None

def frame(r):
    # helpers to place points in the oriented rectangle: a along the long side
    return lambda a, b, z: [r["x"] + r["ux"] * a + r["vx"] * b,
                            r["y"] + r["uy"] * a + r["vy"] * b, z]

WATER = {"pool": "#3AB2C6", "fountain": "#4AA6B4", "water": "#3F6E78"}

def site_features():
    # Pools, fountains and ponds inside the outline (in courtyards): water with a rim
    for f in params.get("features") or []:
        fp = f["footprint"]
        kind = f["kind"]
        part({"pool": "pools", "fountain": "fountains"}.get(kind, "water"))
        if kind != "water":
            face([[p[0], p[1], 0.08] for p in offset_polygon(fp, 1.2)], mat="stone",
                 color="#ECE7DC")
        face([[p[0], p[1], 0.14] for p in fp], mat="glass", color=WATER[kind])
        if kind == "fountain":
            c = polygon_centroid(fp)
            cylinder(c[0], c[1], 0.1, 0.6, 1.4, mat="stone", segments=8, radius_top=0.3)
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
            mats.append("seat_riser")
            colors.append(g("seat_color", None))
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
    # Floodlights under the front edge of the roof, aimed at the field.
    k = max(1, int(L / 12))
    for i in range(k):
        a = -L / 2 + L * (i + 0.5) / k
        x = fx + tx * a + nx * (over - 0.5)
        y = fy + ty * a + ny * (over - 0.5)
        light(x, y, roof_h - 1.3, nx * 30, ny * 30, -roof_h, color="white", intensity=40000,
              beam=50)

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

def tier(prof, mats, colors, a0, z0, a1, z1, row_d, seat):
    # Rows of seats falling from (a0, z0) at the back to (a1, z1) at the front:
    # a riser and a tread per row (the seat texture shows a row every 0.8 m).
    n = max(1, int((a1 - a0) / row_d + 0.5))
    for i in range(n):
        za = z0 - (z0 - z1) * i / n
        zb = z0 - (z0 - z1) * (i + 1) / n
        xb = a0 + (a1 - a0) * (i + 1) / n
        prof.append([xb, za])
        mats.append("seats")
        colors.append(seat)
        prof.append([xb, zb])
        mats.append("seat_riser")
        colors.append(seat)

def stadium():
    ring = footprint
    if len(ring) > 60:  # very detailed outlines: even points every few metres
        ring = resample(ring + [ring[0]], 6.0)[:-1]
    r = oriented_rect(ring)
    W = r["width"]
    depth = clamp(W * 0.22, 10, min(48, W * 0.3))
    H = g("height", clamp(W * 0.17, 10, 48))
    facade = photo_walls() or g("wall_material", "lamella")
    seat = g("seat_color", None)
    row_d = {"low": 2.4, "medium": 1.6, "high": 0.8}.get(params.get("detail"), 1.6)
    z_top = H - 2.5
    z_bot = 1.2
    x0 = 2.5  # walkway behind the top row
    # Glazed entrance level, the facade above, a walkway behind the top row.
    prof = [[0, 0], [0, 4.5], [0, H], [x0, H], [x0, z_top]]
    mats = ["glass", facade, "concrete", "concrete"]
    colors = [None, g("wall_color", "#DADADA"), None, None]
    if depth >= 22:
        # Two tiers: the upper one steeper, a fascia and a concourse with the
        # vomitories (the openings to the stairs) in between.
        split = x0 + (depth - x0) * 0.45
        z_mid = z_bot + (z_top - z_bot) * 0.42
        tier(prof, mats, colors, x0, z_top, split, z_mid + 2.4, row_d, seat)
        prof.append([split, z_mid])  # fascia of the upper tier (advertising band)
        mats.append("stand_fascia")
        colors.append(None)
        prof.append([split + 3.0, z_mid])  # concourse walkway
        mats.append("concrete")
        colors.append(None)
        prof.append([split + 3.0, z_mid - 0.3])
        mats.append("concrete")
        colors.append(None)
        tier(prof, mats, colors, split + 3.0, z_mid - 0.3, depth, z_bot, row_d, seat)
    else:
        tier(prof, mats, colors, x0, z_top, depth, z_bot, row_d, seat)
    prof.append([depth, 0])  # front wall to the pitch
    mats.append("concrete")
    colors.append(None)
    mats.append("concrete")  # closing edge along the ground
    colors.append(None)
    line = [[p[0], p[1], 0] for p in ring]
    part("stands")
    sweep(line, prof, mats=mats, colors=colors, closed_path=True, caps=True)

    # Roof ring: solid outer part, translucent inner edge (polycarbonate/ETFE),
    # cantilever girders underneath and floodlights along its inner edge.
    roof_mat = params.get("roof_material") or "membrane"
    reach = depth * 0.85
    solid = reach * 0.6
    zo = H + 3.0  # outer edge
    zi = H + 1.2  # inner edge
    zs = zo + (zi - zo) * (solid + 1.5) / (reach + 1.5)
    part("roof")
    sweep(line, [[-1.5, zo], [solid, zs], [solid, zs + 0.4], [-1.5, zo + 0.4]],
          mats=[roof_mat, "steel", roof_mat, "steel"],
          colors=[g("roof_color", None), None, g("roof_color", None), None],
          closed_path=True, caps=True)
    sweep(line, [[solid, zs + 0.2], [reach, zi + 0.2]], mat="roof_clear", closed_path=True,
          two_sided=True)
    sweep(line, [[reach, zi - 0.4], [reach, zi + 0.4]], mat="steel", closed_path=True,
          two_sided=True)  # compression ring at the inner edge
    cx = r["x"]
    cy = r["y"]
    n = len(ring)
    perimeter = 0.0
    for i in range(n):
        perimeter += hypot(ring[(i + 1) % n][0] - ring[i][0], ring[(i + 1) % n][1] - ring[i][1])
    every = max(1, int(14.0 / (perimeter / n) + 0.5))  # a girder every ~14 m
    for i in range(0, n, every):
        p = ring[i]
        q = ring[(i + 1) % n]
        o = ring[i - 1]
        tx = q[0] - o[0]
        ty = q[1] - o[1]
        tl = hypot(tx, ty)
        if tl < 0.01:
            continue
        lx = -ty / tl  # left of the walking direction = towards the pitch
        ly = tx / tl
        outer = [p[0] - lx * 1.0, p[1] - ly * 1.0, zo - 0.3]
        inner = [p[0] + lx * reach, p[1] + ly * reach, zi - 0.4]
        tube(outer, inner, 0.25, mat="steel", segments=4)
        tube([p[0] + lx * 1.5, p[1] + ly * 1.5, zo - 1.8], inner, 0.15, mat="steel", segments=4)
        light(inner[0], inner[1], inner[2] - 0.3, cx - inner[0], cy - inner[1], -inner[2],
              color="white", intensity=70000, beam=45)
        # under the roof: light falling on the stands
        light(p[0] + lx * reach * 0.6, p[1] + ly * reach * 0.6, zs - 1.0, lx * 0.3, ly * 0.3, -1,
              color="white", intensity=15000, beam=110, glow=False)
    # The facade lit from below at night.
    part("floodlights")
    floodlight(ring, 0, H, spacing=16, color="white", intensity=6000)

# Facades with real windows (facade() tool) per wall material, and which plain
# materials still get windows cut into them.
FACADE_OF = {"windows": "modern", "windows_house": "house", "windows_classic": "classic",
             "windows_arched": "arched"}
WINDOWED = ["plaster", "brick", "stone", "concrete", "wood"]
STYLE_FACADE = {"house": "house", "historic": "classic", "apartments": "modern",
                "office": "modern", "generic": "modern", "church": "church"}

def walls(fp, z0, H, look, fstyle, top, top_color, holes, levels):
    wall, wall_mat = look[0], look[1]
    raised = z0 > 0.3
    if fstyle != None and H - z0 >= 2.4:
        # Raised blocks (upper tower stages, wings over a passage) get windows too,
        # but no door, and their underside closed.
        doors = int((params.get("tags") or {}).get("xpsb:row_of") or 1)
        facade(fp, z0, H, style=fstyle, storeys=None if raised else levels, color=wall,
               holes=holes, top=top, top_color=top_color, entrance=0 if raised else doors,
               mat=(None if wall_mat in FACADE_OF else wall_mat))
        if raised:
            extrude(fp, z0, z0 + 0.1, mat=look[2], color=wall, top=None, bottom=True,
                    holes=holes)
    else:
        extrude(fp, z0, H, mat=wall_mat, top=top, color=wall, top_color=top_color,
                bottom=raised, holes=holes)

def block(fp, z0, H, shape, look, flip, fstyle=None, holes=None, levels=None, chimney=False,
          tag="", roof_h=None):
    # One building block: walls from z0 to H and a roof (gabled/hipped/pyramidal/flat).
    # Its parts are named "walls", "roof", "chimneys" (+ tag: " 2" for OSM part 2).
    wall, wall_mat, gable_mat, roof_color, pitched, flat, rise_k, rise_max = look
    if H <= z0 + 0.2:
        return
    part("walls" + tag)
    holes = holes or []
    r = oriented_rect(fp)
    if flip:
        # ridge across instead of along the long side
        r = {"x": r["x"], "y": r["y"], "length": r["width"], "width": r["length"],
             "ux": r["vx"], "uy": r["vy"], "vx": -r["vy"], "vy": r["vx"]}
    rect_like = polygon_area(fp) > 0.85 * r["length"] * r["width"] and not holes
    if shape in ("gabled", "hipped") and rect_like:
        walls(fp, z0, H, look, fstyle, None, None, holes, levels)
        part("roof" + tag)
        P = frame(r)
        L2 = r["length"] / 2
        W2 = r["width"] / 2
        rise = roof_h or min(r["width"] * rise_k, rise_max)
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
        if chimney and r["width"] > 5:
            part("chimneys" + tag)
            chimneys(fp, H, H + rise + 1.0, count=max(1, min(6, int(r["length"] / 12) + 1)),
                     size=0.7 if r["width"] < 14 else 1.0)
    elif shape in ("dome", "onion") and not holes:
        # a real dome over any outline: rings drawn in towards the centre
        walls(fp, z0, H, look, fstyle, None, None, holes, levels)
        part("dome" + tag)
        c = polygon_centroid(fp)
        step = max(1, len(fp) // 64)
        ring = [fp[i] for i in range(0, len(fp), step)]
        rise = roof_h or min(min(r["width"], r["length"]) * 0.5, 30)
        if shape == "onion":
            prof = [[1.0, 0.0], [1.12, 0.18], [1.08, 0.38], [0.8, 0.58], [0.45, 0.76], [0.18, 0.9],
                    [0.04, 1.0]]
        else:
            prof = [[1.0, 0.0], [0.97, 0.25], [0.87, 0.5], [0.71, 0.71], [0.5, 0.87], [0.26, 0.97],
                    [0.0, 1.0]]
        rings = [[[c[0] + (p[0] - c[0]) * k, c[1] + (p[1] - c[1]) * k, H + rise * z] for p in ring]
                 for k, z in prof]
        n = len(ring)
        for j in range(len(rings) - 1):
            lo = rings[j]
            hi = rings[j + 1]
            for i in range(n):
                i2 = (i + 1) % n
                if j == len(rings) - 2:
                    face([lo[i], lo[i2], hi[i]], mat=pitched, color=roof_color)
                else:
                    quad(lo[i], lo[i2], hi[i2], hi[i], mat=pitched, color=roof_color)
    elif shape in ("pyramidal", "dome", "onion", "cone") and len(fp) <= 40 and not holes:
        # towers and domes: a pointed roof over the centre
        walls(fp, z0, H, look, fstyle, None, None, holes, levels)
        part("roof" + tag)
        c = polygon_centroid(fp)
        rise = roof_h or min(min(r["width"], r["length"]) * (0.9 if shape == "cone" else 0.6), 25)
        apex = [c[0], c[1], H + rise]
        n = len(fp)
        for i in range(n):
            a = fp[i]
            b = fp[(i + 1) % n]
            face([[a[0], a[1], H], [b[0], b[1], H], apex], mat=pitched, color=roof_color)
    elif shape in ("gabled", "hipped", "mansard") and r["width"] > 9:
        # big or irregular blocks (wings around courtyards): slopes all round
        walls(fp, z0, H, look, fstyle, None, None, holes, levels)
        part("roof" + tag)
        rise = roof_h or min(r["width"] * rise_k * 0.5, rise_max, 4.5)
        mansard(fp, H, rise=rise, depth=min(5.0, r["width"] * 0.25), mat=pitched,
                color=roof_color, holes=holes, top=flat, top_color=roof_color)
        if chimney:
            part("chimneys" + tag)
            chimneys(fp, H, H + rise + 1.0, holes=holes)
    else:
        walls(fp, z0, H, look, fstyle, flat, roof_color, holes, levels)

def main():
    site_features()
    st = style()
    if st == "stadium":
        stadium()
        return
    H0, storey, wall_def, wall_col, shape_def, pitched_def, flat_def, rise_k, rise_max = STYLES[st]
    H = params.get("height")
    levels = params.get("levels")
    if H == None:
        H = levels * storey if levels else H0
    wall = g("wall_color", wall_col)
    wall_mat = g("wall_material", wall_def)
    gable_mat = "plaster" if wall_mat in ("windows", "glass", "metal_sheet") else wall_mat
    if wall_mat in FACADE_OF:
        gable_mat = "plaster"
    if st == "hall":
        gable_mat = wall_mat
    fstyle = FACADE_OF.get(wall_mat)
    if fstyle == None and wall_mat in WINDOWED:
        fstyle = STYLE_FACADE.get(st)
    if photo_walls() != None:  # real facade photos instead of painted/modelled windows
        wall_mat = photo_walls()
        fstyle = None
        gable_mat = "plaster"
    elif params.get("street_photos"):  # street photos are painted over flat walls
        fstyle = None
    roof_color = g("roof_color", None)
    roof_mat = params.get("roof_material")
    look = (wall, wall_mat, gable_mat, roof_color, roof_mat or pitched_def, roof_mat or flat_def,
            rise_k, rise_max)
    chimney = st in ("house", "historic") and roof_mat != "solar"
    parts = params.get("parts") or []
    if parts:
        # OSM building parts (towers, wings, raised roofs): each its own block
        for i, bp in enumerate(parts):
            ph = bp.get("height") or H
            pz = bp.get("min_height") or 0
            # OSM: the height includes the roof (roof:height of it)
            rh = bp.get("roof_height")
            if rh and bp.get("roof_shape") not in (None, "flat") and ph - rh > pz + 1:
                ph = ph - rh
            plook = (bp.get("wall_color") or wall, wall_mat, gable_mat,
                     bp.get("roof_color") or roof_color, look[4], look[5], rise_k, rise_max)
            block(bp["footprint"], pz, ph, bp.get("roof_shape") or "flat", plook, False,
                  fstyle, tag=" %d" % (i + 1), roof_h=rh)
        if g("parts_cover", 1.0) >= 0.8:
            return  # the parts are the whole building
    shape = g("roof_shape", shape_def)
    block(footprint, g("min_height", 0), H, shape, look, g("flip", False), fstyle, courtyards,
          levels, chimney, roof_h=params.get("roof_height"))
    tags = params.get("tags") or {}
    landmark = (tags.get("historic") or tags.get("heritage") or tags.get("tourism")
                or tags.get("wikidata") or tags.get("site:main") == "yes")
    if st in ("historic", "church") and landmark and g("min_height", 0) < 0.5:
        part("floodlights")
        floodlight(footprint, 0, H, spacing=11)  # landmarks are lit at night

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
    if f != None:  # aimed at the field, 40 m away and down
        d = -1 if g("flip", False) else 1
        for k in range(3):
            light((k - 1) * 1.0, 0, H - 0.6, f[0] * 40 * d, f[1] * 40 * d, -H, color="white",
                  intensity=60000, beam=40)

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


MONUMENT = (
    COMMON
    + """
# params["monument"]: kind (statue|rider|bust|obelisk|column|stone|sculpture), material,
# robe, pose, holding. height = the whole monument, pedestal included.
def main():
    m = params.get("monument") or {}
    kind = m.get("kind") or "statue"
    mat = m.get("material") or "bronze"
    x, y, size = 0.0, 0.0, 0.0
    if footprint != None:
        c = polygon_centroid(footprint)
        x, y = c[0], c[1]
        r = oriented_rect(footprint)
        size = min(r["length"], r["width"])
    f = params.get("facing")
    heading = atan2(f[0], f[1]) if f != None else 180  # statues mostly face south
    if g("flip", False):
        heading += 180
    defaults = {"statue": 6.0, "rider": 8.0, "bust": 3.0, "obelisk": 10.0, "column": 15.0,
                "stone": 2.2, "sculpture": 3.0}
    H = g("height", defaults.get(kind, 6.0))
    stone = "stone" if mat == "bronze" else mat
    if has_scan:  # the real statue, scanned (usually with the base it stands on)
        scan(x, y, 0, H, heading=heading, mat=mat)
        return
    if kind == "obelisk":
        w = size * 0.6 if size > 0 else max(0.8, H * 0.1)
        top = pedestal(x, y, H * 0.12, w * 1.6, heading=heading, mat=stone)
        revolve(x, y, [[w * 0.7, top], [w * 0.5, H * 0.92], [0, H]], mat=stone, segments=4,
                start=45 + heading, end=405 + heading)
        return
    if kind == "column":
        w = max(0.8, H * 0.07)
        top = pedestal(x, y, H * 0.1, w * 2.6, heading=heading, mat=stone)
        cylinder(x, y, top, w / 2, H * 0.62, mat="stone", segments=segs(16))
        box(x, y, top + H * 0.62, w * 1.4, w * 1.4, H * 0.03, heading=heading, mat="stone",
            top="stone")
        figure(x, y, top + H * 0.65, H * 0.25, pose="standing", heading=heading,
               mat=mat, robe=m.get("robe") == True, holding=m.get("holding") or "none")
        return
    if kind == "sculpture":
        # an animal or abstract work: a smooth form on a low base, never a person
        w = size * 0.8 if size > 0 else max(1.0, H * 0.6)
        top = pedestal(x, y, min(0.6, H * 0.15), w, heading=heading, mat=stone)
        h = H - top
        revolve(x, y, [[w * 0.32, top], [w * 0.42, top + h * 0.25], [w * 0.3, top + h * 0.55],
                       [w * 0.36, top + h * 0.75], [w * 0.18, top + h * 0.95], [0, top + h]],
                mat=mat, segments=segs(12))
        return
    if kind == "stone":
        box(x, y, 0, max(0.3, H * 0.25), max(0.8, H * 0.45), H, heading=heading, mat=stone,
            top=stone)
        return
    share = {"statue": 0.4, "rider": 0.35, "bust": 0.55}.get(kind, 0.4)
    pw = size if size > 0 else max(1.0, H * (0.45 if kind == "rider" else 0.3))
    if kind == "rider":
        top = pedestal(x, y, H * share, pw * 0.6, pw, heading=heading, mat=stone)
    else:
        top = pedestal(x, y, H * share, pw, heading=heading, mat=stone,
                       round=kind == "bust")
    pose = m.get("pose") or "standing"
    if kind in ("rider", "bust"):
        pose = kind
    figure(x, y, top, H - top, pose=pose, heading=heading, mat=mat,
           robe=m.get("robe") == True, holding=m.get("holding") or "none")

main()
"""
)

BRIDGE = (
    COMMON
    + """
# Bridges: params.bridge = structure (beam/arch/truss/suspension/cable_stayed),
# material, width, carries (road/rail/foot), historic, deck ([x, y, z] of the
# deck's centre every few metres: it meets the banks), ground ([x, y, z] of the
# ground along it, may be empty), approaches (roads leading onto it, [x, y,
# ground z], from the bridge outwards). Heights are relative to the ground at the
# object's origin; footprint-only bridges (drawn by hand) use height.

def measure(pts):
    d = [0.0]
    for i in range(1, len(pts)):
        d.append(d[-1] + hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]))
    return d

def at(pts, d, s):
    # point [x, y, z] and unit direction at distance s along the polyline
    for i in range(1, len(pts)):
        if d[i] >= s or i == len(pts) - 1:
            seg = max(d[i] - d[i - 1], 0.001)
            t = clamp((s - d[i - 1]) / seg, 0, 1)
            dx = (pts[i][0] - pts[i - 1][0]) / seg
            dy = (pts[i][1] - pts[i - 1][1]) / seg
            return [pts[i - 1][0] + (pts[i][0] - pts[i - 1][0]) * t,
                    pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * t,
                    pts[i - 1][2] + (pts[i][2] - pts[i - 1][2]) * t], [dx, dy]
    return pts[0], [1.0, 0.0]

def side(p, u, off, z):
    # off > 0: left of the walking direction
    return [p[0] - u[1] * off, p[1] + u[0] * off, z]

def heading(u):
    return atan2(u[0], u[1])

def ground_at(gr, x, y):
    best = 0.0
    bd = 1e18
    for q in gr:
        dd = (q[0] - x) * (q[0] - x) + (q[1] - y) * (q[1] - y)
        if dd < bd:
            bd = dd
            best = q[2]
    return best

def deck_points(b):
    deck = b.get("deck")
    if deck != None and len(deck) >= 2:
        return deck
    H = g("height", 6.0)
    if path != None and len(path) >= 2:
        return [[p[0], p[1], H] for p in path]
    r = oriented_rect(footprint)
    L2 = r["length"] / 2
    return [[r["x"] - r["ux"] * L2, r["y"] - r["uy"] * L2, H],
            [r["x"] + r["ux"] * L2, r["y"] + r["uy"] * L2, H]]

def parapets(line, W, solid, mat, color, historic):
    for sgn in (-1, 1):
        o = sgn * W / 2
        if solid:
            prof = [[o - 0.2 * sgn, 0], [o - 0.2 * sgn, 1.0], [o + 0.2 * sgn, 1.0],
                    [o + 0.2 * sgn, -0.3]]
            pm = "balustrade" if historic and mat == "stone" else mat
            sweep(line, prof, mats=[pm, "moulding" if historic else mat, pm, mat],
                  color=color, caps=True)
        else:
            sweep(line, [[o - 0.04, 1.05], [o - 0.04, 1.15], [o + 0.04, 1.15],
                         [o + 0.04, 1.05]], mat="steel", caps=True)
            sweep(line, [[o - 0.02, 0.45], [o - 0.02, 0.5], [o + 0.02, 0.5],
                         [o + 0.02, 0.45]], mat="steel", caps=True)

def railing_posts(pts, d, W, L):
    n = int(L / 2.5)
    for i in range(n + 1):
        p, u = at(pts, d, L * i / max(n, 1))
        for sgn in (-1, 1):
            q = side(p, u, sgn * W / 2, p[2])
            cylinder(q[0], q[1], p[2], 0.03, 1.15, mat="steel", segments=4)

def pier(p, u, z0, z1, W, thick, mat, color):
    if z1 - z0 < 0.5:
        return
    box(p[0], p[1], z0, thick, W, z1 - z0, heading=heading(u), mat=mat, color=color)

def arches(pts, d, gr, spans, W, t, mat, color):
    # stone/brick arches between the piers: the two spandrel walls and the vault
    n = segs(8)
    for a, b in spans:
        span = b - a
        pa, ua = at(pts, d, a)
        pb, ub = at(pts, d, b)
        low = max(ground_at(gr, pa[0], pa[1]), ground_at(gr, pb[0], pb[1]))
        top = min(pa[2], pb[2]) - t
        rise = min(span * 0.42, top - low - 1.0)
        if rise < 1.0:
            continue
        prev = None
        for k in range(n + 1):
            s = a + span * k / n
            p, u = at(pts, d, s)
            spring = top - rise - 0.6
            z = spring + rise * sin(180.0 * k / n)
            cur = [side(p, u, W / 2, z), side(p, u, -W / 2, z),
                   side(p, u, W / 2, p[2] - t), side(p, u, -W / 2, p[2] - t)]
            if prev != None:
                quad(prev[0], cur[0], cur[2], prev[2], mat=mat, color=color)
                quad(cur[1], prev[1], prev[3], cur[3], mat=mat, color=color)
                quad(prev[1], cur[1], cur[0], prev[0], mat=mat, color=color)
            prev = cur

def truss(pts, d, L, W):
    th = clamp(W * 0.7, 5.0, 10.0)
    panel = clamp(L / max(1, int(L / 7.0 + 0.5)), 4.0, 9.0)
    n = max(1, int(L / panel + 0.5))
    for sgn in (-1, 1):
        o = sgn * (W / 2 + 0.3)
        tops = []
        bots = []
        for i in range(n + 1):
            p, u = at(pts, d, L * i / n)
            tops.append(side(p, u, o, p[2] + th))
            bots.append(side(p, u, o, p[2]))
        for i in range(n):
            tube(tops[i], tops[i + 1], 0.3, mat="steel", segments=4)
            tube(bots[i], bots[i + 1], 0.3, mat="steel", segments=4)
            tube(bots[i], tops[i], 0.2, mat="steel", segments=4)
            if i % 2 == 0:
                tube(bots[i], tops[i + 1], 0.18, mat="steel", segments=4)
            else:
                tube(tops[i], bots[i + 1], 0.18, mat="steel", segments=4)
        tube(bots[n], tops[n], 0.2, mat="steel", segments=4)
    for i in range(0, n + 1, 2):  # cross bracing over the deck
        p, u = at(pts, d, L * i / n)
        tube(side(p, u, W / 2 + 0.3, p[2] + th), side(p, u, -W / 2 - 0.3, p[2] + th), 0.18,
             mat="steel", segments=4)

def suspension(pts, d, gr, L, W, mat, color):
    sa = L * 0.18
    sb = L * 0.82
    main = sb - sa
    pa, _ = at(pts, d, sa)
    pb, _ = at(pts, d, sb)
    th = max(pa[2], pb[2]) + clamp(main * 0.11, 12.0, 60.0)
    for s in (sa, sb):
        p, u = at(pts, d, s)
        base = ground_at(gr, p[0], p[1]) - 4.0
        for sgn in (-1, 1):
            q = side(p, u, sgn * (W / 2 + 0.8), 0)
            box(q[0], q[1], base, 3.0, 2.2, th - base, heading=heading(u), mat=mat,
                color=color)
        a = side(p, u, W / 2 + 0.8, th - 1.5)
        b = side(p, u, -W / 2 - 0.8, th - 1.5)
        mid = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2, th - 1.5]
        box(mid[0], mid[1], th - 3.0, 3.0, W + 3.6, 2.5, heading=heading(u), mat=mat,
            color=color)
    pm, _ = at(pts, d, L / 2)
    sag_low = pm[2] + 2.0
    m = max(4, int(L / 8.0))
    for sgn in (-1, 1):
        o = sgn * (W / 2 + 0.8)
        prev = None
        for i in range(m + 1):
            s = L * i / m
            p, u = at(pts, d, s)
            if s < sa:  # back stay down to the anchorage at the bank
                z = p[2] + 0.5 + (th - 0.5 - p[2] - 0.5) * s / sa
            elif s > sb:
                z = p[2] + 0.5 + (th - 0.5 - p[2] - 0.5) * (L - s) / (L - sb)
            else:
                x = (s - sa) / main * 2 - 1
                z = sag_low + (th - 0.5 - sag_low) * x * x
            cur = side(p, u, o, z)
            if prev != None:
                tube(prev, cur, 0.35, mat="steel", segments=6)
            if sa < s and s < sb and z - p[2] > 1.2:
                tube(cur, side(p, u, o, p[2]), 0.05, mat="steel", segments=3)
            prev = cur

def cable_stayed(pts, d, gr, L, W, mat, color):
    towers = [L / 2] if L < 250 else [L * 0.3, L * 0.7]
    reach = L / (2 * len(towers))
    for s in towers:
        p, u = at(pts, d, s)
        th = p[2] + clamp(reach * 0.45, 15.0, 120.0)
        base = ground_at(gr, p[0], p[1]) - 4.0
        box(p[0], p[1], base, 4.0, 3.0, th - base, heading=heading(u), mat=mat, color=color)
        n = max(3, int(reach / 12.0))
        for i in range(1, n + 1):
            for dirn in (-1, 1):
                sp = s + dirn * reach * 0.95 * i / n
                if sp < 1 or sp > L - 1:
                    continue
                q, v = at(pts, d, sp)
                for sgn in (-1, 1):
                    tube([p[0], p[1], th - 1.0 - i * 0.6], side(q, v, sgn * W / 2, q[2]),
                         0.08, mat="steel", segments=3)

def approach(pts, z_end, W, top_mat, wall_mat, color):
    # a ramp following the road from the bridge end down to its ground
    if len(pts) < 2:
        return
    drop = z_end - pts[0][2]
    if drop < 0.4:
        return  # the road is already at deck height there
    d = measure(pts)
    run = min(d[-1], max(20.0, drop / 0.06))
    line = []
    flat = [[p[0], p[1]] for p in pts]
    for q in resample(flat, 3.0):
        dist = 0.0
        if len(line) > 0:
            dist = line[-1][3] + hypot(q[0] - line[-1][0], q[1] - line[-1][1])
        if dist > run:
            break
        g0 = ground_at(pts, q[0], q[1])
        t = dist / run
        z = g0 + (z_end - pts[0][2]) * (0.5 + 0.5 * cos(180.0 * t))  # eases in and out
        line.append([q[0], q[1], max(g0 + 0.05, z), dist])
    if len(line) < 2:
        return
    path3 = [[q[0], q[1], q[2]] for q in line]
    sweep(path3, [[-W / 2, -drop - 2.0], [-W / 2, 0], [W / 2, 0], [W / 2, -drop - 2.0]],
          mats=[wall_mat, top_mat, wall_mat, wall_mat], colors=[color, None, color, color],
          caps=True)

def main():
    b = params.get("bridge") or {}
    st = b.get("structure") or "beam"
    W = b.get("width") or g("width", 8.0)
    carries = b.get("carries") or "road"
    historic = b.get("historic") or False
    mat = b.get("material") or (
        "stone" if st == "arch" else "steel" if st == "truss" else "concrete")
    color = g("wall_color", None)
    gr = b.get("ground") or []
    pts = deck_points(b)
    d = measure(pts)
    L = d[-1]
    t = 1.2 if mat in ("stone", "brick") else 1.8 if st == "beam" else 1.0
    top = "asphalt" if carries == "road" else "concrete" if carries == "rail" else (
        "stone" if mat in ("stone", "brick") else "concrete")
    line = [[p[0], p[1], p[2]] for p in pts]
    side_mat = mat if mat in ("stone", "brick", "concrete") else "steel"
    sweep(line, [[-W / 2, -t], [-W / 2, 0], [W / 2, 0], [W / 2, -t]],
          mats=[side_mat, top, side_mat, side_mat], colors=[color, None, color, color], caps=True)
    if carries == "road" and W >= 9:  # raised pavements along both sides
        for sgn in (-1, 1):
            o = sgn * W / 2
            i = o - sgn * 2.2
            sweep(line, [[o, 0], [o, 0.18], [i, 0.18], [i, 0]], mat="concrete", caps=True)
    solid = mat in ("stone", "brick") or (historic and mat == "concrete")
    parapets(line, W, solid, side_mat, color, historic)
    if not solid:
        railing_posts(pts, d, W, L)

    # Piers: how far apart depends on how the bridge is carried.
    span = {"arch": 22.0, "beam": 32.0, "truss": 70.0, "suspension": L,
            "cable_stayed": L}.get(st, 30.0)
    if mat in ("stone", "brick"):
        span = min(span, 24.0)
    n = max(1, int(L / span + 0.5))
    span = L / n
    stops = [span * i for i in range(n + 1)]
    thick = clamp(span * 0.16, 2.0, 6.0) if st == "arch" else 2.2
    pier_mat = mat if mat in ("stone", "brick", "concrete") else "concrete"
    if st not in ("suspension", "cable_stayed"):
        for s in stops[1:-1]:
            p, u = at(pts, d, s)
            pier(p, u, ground_at(gr, p[0], p[1]) - 6.0, p[2] - t, W * 0.92, thick, pier_mat,
                 color)
    for s in (0.0, L):  # abutments at both ends
        p, u = at(pts, d, s)
        pier(p, u, ground_at(gr, p[0], p[1]) - 6.0, p[2] - t, W + 0.6, 3.0, pier_mat, color)
    if st == "arch":
        spans = []
        for i in range(n):
            a = stops[i] + (thick / 2 if i > 0 else 1.5)
            e = stops[i + 1] - (thick / 2 if i < n - 1 else 1.5)
            spans.append([a, e])
        arches(pts, d, gr, spans, W, t, pier_mat, color)
    elif st == "truss":
        truss(pts, d, L, W)
    elif st == "suspension":
        suspension(pts, d, gr, L, W, pier_mat, color)
    elif st == "cable_stayed":
        cable_stayed(pts, d, gr, L, W, pier_mat, color)

    # Street lights along road bridges.
    if carries != "rail" and params.get("detail") != "low" and L > 30:
        m = max(1, int(L / 30))
        for i in range(m + 1):
            p, u = at(pts, d, L * i / m)
            for sgn in (-1, 1):
                q = side(p, u, sgn * (W / 2 - 0.4), p[2])
                cylinder(q[0], q[1], p[2], 0.08, 7.0, mat="steel", segments=6, radius_top=0.05)
                light(q[0], q[1], p[2] + 7.0, 0, 0, -1, color="warm", intensity=4000, beam=110)

    # The roads leading onto the bridge: ramps down to their ground.
    ends = [pts[0], pts[-1]]
    for ap in b.get("approaches") or []:
        e = ends[0]
        if hypot(ap[0][0] - ends[1][0], ap[0][1] - ends[1][1]) < hypot(
                ap[0][0] - ends[0][0], ap[0][1] - ends[0][1]):
            e = ends[1]
        approach(ap, e[2], W, top, pier_mat, color)

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
    "monuments": MONUMENT,
    "bridges": BRIDGE,
}
