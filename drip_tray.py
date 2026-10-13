"""Drip tray (plant saucer) for a stadium-shaped wrought-iron planter basket.

The tray is built as one watertight solid, then split along X into N_PARTS
pieces joined by glued half-lap joints. Run:  python drip_tray.py

Outputs (millimetres), next to this script:
  STEP/drip_tray_full_reference.step, STL/drip_tray_full_reference.stl
  STEP/drip_tray_part_<A..>.step,     STL/drip_tray_part_<A..>.stl
  STEP/drip_tray_assembly.step  (parts in assembled position, for checking)
  STEP/drip_tray_exploded.step  (parts spread along X, for review)

Coordinates: origin at footprint centre, long axis X, bottom face on Z = 0.

Half-lap convention
  At each cut one part keeps the OUTER skin and one keeps the INNER skin over an
  overlap of OVERLAP mm centred on the cut. The lap interface is the inner
  cavity offset outward by half the wall/bottom thickness (so the floor-to-wall
  corner of the interface has radius FILLET_INNER + T_WALL/2). The glue gap
  GLUE_GAP is the TOTAL clearance per mating face pair, split evenly: each
  part's removed volume is grown by GLUE_GAP/2 on the lap face and on the
  shoulder/tip faces.
  Roles: end parts always take the inner skin; at a joint between two middle
  parts (N_PARTS >= 4) the left part takes the outer skin.
"""

from __future__ import annotations

import string
from pathlib import Path

import build123d as bd

# ---------------------------------------------------------------- parameters
CLEARANCE = 3.0          # c: inward offset of tray from basket inner profile
BASKET_WIDTH = 190.0     # basket inner width (end diameter)
STRAIGHT = 400.0         # S: straight section length (unchanged by offset)
HEIGHT = 30.0            # H: overall tray height
T_WALL = 3.0             # vertical wall thickness
T_BOTTOM = 3.0           # floor thickness
N_PARTS = 3              # number of printed parts along X
OVERLAP = 15.0           # half-lap overlap length along X, centred on the cut
GLUE_GAP = 0.15          # total clearance on every mating face pair
FILLET_INNER = 3.0       # floor-to-wall fillet inside the cavity
FILLET_OUTER = 1.5       # outer bottom edge fillet
BED_LIMIT = 245.0        # max allowed part X/Y extent (250 bed with margin)

# ---------------------------------------------------------------- derived
W = BASKET_WIDTH - 2 * CLEARANCE          # outer width (184)
R = W / 2                                 # end radius (92)
L = STRAIGHT + W                          # outer length (584)
W_IN = W - 2 * T_WALL                     # cavity width (178)
L_IN = STRAIGHT + W_IN                    # cavity length (578)
CUTS = [-L / 2 + k * L / N_PARTS for k in range(1, N_PARTS)]
NAMES = list(string.ascii_uppercase[:N_PARTS])

_HERE = Path(__file__).resolve().parent
_BIG = 10 * L  # generous extent for cutting boxes

_REACH = OVERLAP / 2 + GLUE_GAP / 2
for _xc in CUTS:
    if abs(_xc) + _REACH > STRAIGHT / 2:
        raise ValueError(
            f"cut at x={_xc:.2f} and its lap reach outside the straight section "
            f"(|x| must stay < {STRAIGHT / 2}); reduce N_PARTS or OVERLAP"
        )


# ---------------------------------------------------------------- geometry
def _stadium_prism(length: float, width: float, z0: float, z1: float) -> bd.Part:
    sketch = bd.Plane.XY.offset(z0) * bd.SlotOverall(length, width)
    return bd.extrude(sketch, amount=z1 - z0)


def cavity_offset(d_side: float, d_bottom: float, top: float = HEIGHT + 1.0) -> bd.Part:
    """Inner cavity grown outward by d_side (walls) and d_bottom (floor).

    d = 0 is the real cavity. Its floor edge carries FILLET_INNER + d_side, so
    with equal wall/floor offsets this is the exact offset surface.
    """
    tool = _stadium_prism(L_IN + 2 * d_side, W_IN + 2 * d_side, T_BOTTOM - d_bottom, top)
    radius = FILLET_INNER + d_side
    if radius > 0:
        tool = bd.fillet(tool.edges().group_by(bd.Axis.Z)[0], radius)
    return tool


def full_tray() -> bd.Part:
    outer = _stadium_prism(L, W, 0.0, HEIGHT)
    outer = bd.fillet(outer.edges().group_by(bd.Axis.Z)[0], FILLET_OUTER)
    return outer - cavity_offset(0.0, 0.0)


def _x_box(x0: float, x1: float) -> bd.Part:
    return bd.Pos((x0 + x1) / 2, 0, HEIGHT / 2) * bd.Box(x1 - x0, _BIG, HEIGHT + 20)


def _outer_role(left: int, right: int) -> int:
    """Index of the part that keeps the OUTER skin at the joint left|right."""
    if left == 0:
        return right
    return left


def make_part(i: int, tray: bd.Part) -> bd.Solid:
    x_lo = -_BIG if i == 0 else CUTS[i - 1] - OVERLAP / 2 + GLUE_GAP / 2
    x_hi = _BIG if i == N_PARTS - 1 else CUTS[i] + OVERLAP / 2 - GLUE_GAP / 2
    part = tray & _x_box(x_lo, x_hi)

    joints = []
    if i > 0:
        joints.append((CUTS[i - 1], +1, _outer_role(i - 1, i) == i))  # part right of cut
    if i < N_PARTS - 1:
        joints.append((CUTS[i], -1, _outer_role(i, i + 1) == i))      # part left of cut

    for xc, side, is_outer in joints:
        shift = side * GLUE_GAP / 2
        zone = _x_box(xc - OVERLAP / 2 + shift, xc + OVERLAP / 2 + shift)
        if is_outer:
            # keep outer skin: remove material inside the (grown) interface
            grow = (T_WALL / 2 + GLUE_GAP / 2, T_BOTTOM / 2 + GLUE_GAP / 2)
            part = part - (zone & cavity_offset(*grow))
        else:
            # keep inner skin: remove material outside the (shrunk) interface
            keep = (T_WALL / 2 - GLUE_GAP / 2, T_BOTTOM / 2 - GLUE_GAP / 2)
            part = part - (zone - cavity_offset(*keep))

    solids = part.solids()
    if len(solids) != 1:
        raise RuntimeError(f"part {NAMES[i]} has {len(solids)} solids, expected 1")
    return solids[0]


COLORS = ["#4f81bd", "#c0504d", "#9bbb59", "#8064a2", "#f79646", "#4bacc6"]


def build_all():
    tray = full_tray()
    parts = [make_part(i, tray) for i in range(N_PARTS)]
    return tray, parts


def _export(shape: bd.Shape, stem: str) -> None:
    (_HERE / "STEP").mkdir(exist_ok=True)
    (_HERE / "STL").mkdir(exist_ok=True)
    bd.export_step(shape, str(_HERE / "STEP" / f"{stem}.step"))
    bd.export_stl(shape, str(_HERE / "STL" / f"{stem}.stl"),
                  tolerance=0.01, angular_tolerance=0.1)


def main() -> None:
    tray, parts = build_all()
    tray.label = "drip_tray_full_reference"
    _export(tray, "drip_tray_full_reference")

    placed, exploded = [], []
    for i, (name, part) in enumerate(zip(NAMES, parts)):
        part.label = f"part_{name}"
        part.color = bd.Color(COLORS[i % len(COLORS)])
        _export(part, f"drip_tray_part_{name}")
        placed.append(part)
        spread = (i - (N_PARTS - 1) / 2) * 60.0
        moved = part.moved(bd.Location((spread, 0, 0)))
        moved.label = f"part_{name}"
        moved.color = part.color
        exploded.append(moved)

    bd.export_step(bd.Compound(children=placed, label="drip_tray_assembly"),
                   str(_HERE / "STEP" / "drip_tray_assembly.step"))
    bd.export_step(bd.Compound(children=exploded, label="drip_tray_exploded"),
                   str(_HERE / "STEP" / "drip_tray_exploded.step"))

    print(f"W={W} R={R} L={L} H={HEIGHT}; cuts at {[round(c, 3) for c in CUTS]}")
    for name, part in zip(NAMES, parts):
        bb = part.bounding_box()
        print(f"part {name}: {bb.size.X:.3f} x {bb.size.Y:.3f} x {bb.size.Z:.3f} mm, "
              f"V={part.volume / 1000:.1f} cm3")


if __name__ == "__main__":
    main()
