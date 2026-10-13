"""Verify the saved drip tray outputs. Run after drip_tray.py:

    python checks/check_drip_tray.py
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import build123d as bd
import trimesh
from OCP.BOPAlgo import BOPAlgo_ArgumentAnalyzer
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.gp import gp_Pnt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import drip_tray as dt  # noqa: E402  (parameters and derived values)

FAILS: list[str] = []


def check(ok: bool, msg: str) -> None:
    print(("PASS  " if ok else "FAIL  ") + msg)
    if not ok:
        FAILS.append(msg)


def load(stem: str) -> bd.Solid:
    shape = bd.import_step(str(ROOT / "STEP" / f"{stem}.step"))
    solids = shape.solids()
    assert len(solids) == 1, f"{stem}: {len(solids)} solids"
    return solids[0]


def dist(a, b) -> float:
    d = BRepExtrema_DistShapeShape(a.wrapped if hasattr(a, "wrapped") else a, b.wrapped)
    d.Perform()
    return d.Value()


def brep_ok(s: bd.Solid) -> tuple[bool, bool]:
    valid = BRepCheck_Analyzer(s.wrapped).IsValid()
    an = BOPAlgo_ArgumentAnalyzer()
    an.SetShape1(s.wrapped)
    an.SelfInterMode = True
    an.Perform()
    return valid, not an.HasFaulty()


def mesh_report(stem: str, brep_volume: float) -> None:
    m = trimesh.load(ROOT / "STL" / f"{stem}.stl", force="mesh")
    edges_ok = (len(trimesh.grouping.group_rows(m.edges_sorted, require_count=2)) * 2
                == len(m.edges_sorted))
    check(m.is_watertight and m.is_winding_consistent and m.is_volume and edges_ok,
          f"STL {stem}: watertight={m.is_watertight} winding={m.is_winding_consistent} "
          f"every edge shared by exactly 2 faces={edges_ok} faces={len(m.faces)} "
          f"bodies={m.body_count}")
    check(abs(m.volume - brep_volume) / brep_volume < 2e-3 and m.body_count == 1,
          f"STL {stem}: mesh volume {m.volume:.0f} vs BRep {brep_volume:.0f} mm3")


def main() -> None:
    tray = load("drip_tray_full_reference")
    bb = tray.bounding_box()
    target = (dt.L, dt.W, dt.HEIGHT)
    got = (bb.size.X, bb.size.Y, bb.size.Z)
    check(all(abs(g - t) <= 0.1 for g, t in zip(got, target)) and abs(bb.min.Z) < 1e-6,
          f"full tray bbox {got[0]:.3f} x {got[1]:.3f} x {got[2]:.3f} mm "
          f"(target {target}, zmin={bb.min.Z:.3f})")
    v, si = brep_ok(tray)
    check(v and si, f"full tray BRep valid={v}, no self-intersection={si}")
    mesh_report("drip_tray_full_reference", tray.volume)

    parts = [load(f"drip_tray_part_{n}") for n in dt.NAMES]
    for n, p in zip(dt.NAMES, parts):
        pb = p.bounding_box()
        check(pb.size.X <= dt.BED_LIMIT and pb.size.Y <= dt.BED_LIMIT
              and abs(pb.min.Z) < 1e-6 and pb.max.Z <= dt.HEIGHT + 1e-6,
              f"part {n} bbox {pb.size.X:.3f} x {pb.size.Y:.3f} x {pb.size.Z:.3f} mm "
              f"(x {pb.min.X:.3f}..{pb.max.X:.3f}), on Z=0, top <= H")
        v, si = brep_ok(p)
        check(v and si, f"part {n} BRep valid={v}, no self-intersection={si}, "
              f"V={p.volume / 1000:.2f} cm3")
        mesh_report(f"drip_tray_part_{n}", p.volume)

    # ---- assembly: parts as saved are already in assembled position
    for (na, a), (nb, b) in zip(zip(dt.NAMES, parts), zip(dt.NAMES[1:], parts[1:])):
        common = (a & b)
        cv = common.volume if common is not None and common.solids() else 0.0
        # shell-to-shell: the solid-to-solid form runs an inside test that
        # misclassifies points within ~0.1 mm of these thin skins
        dmin = dist(a.shells()[0], b.shells()[0])
        check(cv < 1e-6, f"joint {na}|{nb}: interference volume {cv:.2e} mm3")
        check(abs(dmin - dt.GLUE_GAP) < 0.01,
              f"joint {na}|{nb}: min clearance {dmin:.4f} mm (target {dt.GLUE_GAP})")

    union = parts[0]
    for p in parts[1:]:
        union = union + p
    protrude = union - tray
    pv = protrude.volume if protrude is not None and protrude.solids() else 0.0
    check(pv < 1e-3, f"no part material outside the reference tray ({pv:.2e} mm3)")

    void = tray - union
    voids = void.solids()
    vv = sum(s.volume for s in voids)
    print(f"INFO  glue-gap void: {len(voids)} regions, {vv:.1f} mm3 total")

    # Local gap: sample points on every void face that lies inside the tray
    # (i.e. a mating face, not a seam strip on the tray skin); the distance
    # from that point to the nearest part it does NOT lie on is the gap there.
    # Only part faces near a joint matter; distance to those is much faster.
    def near_joint(f) -> bool:
        fb = f.bounding_box()
        return any(fb.min.X < xc + dt.OVERLAP and fb.max.X > xc - dt.OVERLAP
                   for xc in dt.CUTS)

    joint_faces = [bd.Compound([f for f in p.faces() if near_joint(f)]) for p in parts]
    rng = random.Random(0)
    worst, best, n_samples = 0.0, 1e9, 0
    print(f"INFO  sampling {sum(len(v.faces()) for v in voids)} void faces ...", flush=True)
    for vs in voids:
        for f in vs.faces():
            for _ in range(20):
                p = f.position_at(rng.uniform(0.05, 0.95), rng.uniform(0.05, 0.95))
                vtx = BRepBuilderAPI_MakeVertex(gp_Pnt(p.X, p.Y, p.Z)).Vertex()
                if dist(vtx, f) > 1e-6:
                    continue  # UV sample fell outside the trimmed face
                ds = sorted(dist(vtx, q) for q in joint_faces)
                if ds[0] > 1e-5:
                    continue  # seam strip on the tray skin, not a mating face
                gap = ds[1]
                worst, best = max(worst, gap), min(best, gap)
                n_samples += 1
    check(n_samples > 50 and worst <= 0.2 + 1e-6,
          f"local glue gap over {n_samples} mating-face samples: "
          f"{best:.4f} .. {worst:.4f} mm (max allowed 0.2)")

    # ---- wall/bottom thickness of the assembly away from the joints
    check(abs(dist(bd.Vertex(0, dt.W / 2, 15), dt.cavity_offset(0, 0)) - dt.T_WALL) < 1e-6,
          "nominal wall thickness 3.0 at mid straight section")

    cav = dt.cavity_offset(0.0, 0.0, top=dt.HEIGHT)
    print(f"INFO  inner cavity volume (floor to rim): {cav.volume / 1e6:.3f} L")

    print("\nRESULT:", "ALL PASS" if not FAILS else f"{len(FAILS)} FAIL(S)")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
