"""命令行：从 OSGB 倾斜摄影中提取圆罐并重建白模。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tankextract import __version__
from tankextract.pipeline import RunOptions, run
from tankextract.tools import library_hint, resolve_blender


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tankextract",
        description=(
            "从倾斜摄影 OSGB（ContextCapture / Smart3D 目录或 zip）中自动找出竖直圆柱罐，"
            "在 Blender 里重建为无纹理白模，并按 metadata.xml 的坐标系导出 FBX。"
        ),
    )
    parser.add_argument("--input", "-i", help="OSGB 块目录、zip，或包含多个块的根目录")
    parser.add_argument("--output", "-o", help="输出目录（FBX、tanks.geojson、tanks.csv、tanks.prj）")
    parser.add_argument(
        "--reader",
        choices=("auto", "python", "osgconv"),
        default="auto",
        help=(
            "OSGB 读取方式。auto（默认）：内置解析器，失败时若找到 osgconv 则回退；"
            "python：只用内置解析器；osgconv：全部用 osgconv（本机已验证的读法）"
        ),
    )
    parser.add_argument(
        "--osgconv",
        default=None,
        help=(
            "osgconv 可执行文件。默认依次查找：本参数、$OSGCONV、PATH 中的 osgconv、"
            "~/3dobj_env/osg/bin/osgconv"
        ),
    )
    parser.add_argument(
        "--blender",
        default=None,
        help="Blender 可执行文件。默认查找 PATH，以及 /Applications/Blender.app/Contents/MacOS/Blender",
    )
    parser.add_argument(
        "--lod",
        default="finest",
        help="使用的细节层级：finest（每个瓦片目录里最大的 _L层号_）或具体层号，例如 21",
    )
    parser.add_argument("--min-radius", type=float, default=2.0, help="最小罐半径（米），默认 2.0。更小的多为立管、阀井、小型立式容器")
    parser.add_argument("--max-radius", type=float, default=80.0, help="最大罐半径（米），默认 80")
    parser.add_argument("--min-height", type=float, default=2.0, help="最小壁高（米），默认 2")
    parser.add_argument("--max-height", type=float, default=80.0, help="最大壁高（米），默认 80")
    parser.add_argument("--max-rms", type=float, default=0.20, help="圆拟合允许的最大 RMS 残差（米），默认 0.20")
    parser.add_argument("--min-coverage", type=float, default=260.0, help="圆周覆盖的最小角度，默认 260")
    parser.add_argument(
        "--max-base-above-ground",
        type=float,
        default=3.0,
        help="罐底高出周围地面的最大值（米），超过视为楼顶/构筑物上的设备，默认 3",
    )
    parser.add_argument(
        "--max-aspect",
        type=float,
        default=4.0,
        help="壁高/直径的最大比值，超过视为烟囱或塔器，默认 4",
    )
    parser.add_argument("--cell-size", type=float, default=0.25, help="水平分箱尺寸（米），默认 0.25")
    parser.add_argument("--segments", type=int, default=96, help="圆周分段数。--quality full（回归）时原样使用，默认 96")
    parser.add_argument(
        "--quality", choices=("full", "budget"), default="budget",
        help="budget：只降低罐体/球壳等大曲面的分段，使常见单体面数下降，栏杆、踏步、支腿、开口保留；full：保持 --segments，不减面",
    )
    parser.add_argument(
        "--precision", type=float, default=0.50,
        help="budget 时罐体/拱顶/球壳的弦高目标（米），默认 0.50。不作用于栏杆、踏步、支腿和开口。full 时忽略",
    )
    parser.add_argument(
        "--max-faces", type=int, default=2000,
        help="budget 时一座模型（父级及其网格）的多边形预算，默认 2000。壳先减；超了就保留并写出多边形数、三角数和原因。默认圆管不是 4 边，2000 不是已达成的结果",
    )
    parser.add_argument(
        "--tube-sides", type=int, default=None,
        help="圆管/圆杆断面边数。省略时栏杆用 8 边（full 为 12 边），细杆 8 边、粗杆 12 边。4 边是显式粗选项，断面接近方杆，只在明确要求时使用",
    )
    parser.add_argument("--name-prefix", default="LA", help="罐体导出名前缀，序号沿用 Tank_### 的编号，默认 LA（Tank_007 -> LA0007）")
    parser.add_argument(
        "--material", default="Standard",
        help="FBX 材质名称，默认 Standard。Blender 写出的是同名 Phong/Lambert 表面，不是已经验证过的 3ds Max Standard 材质类",
    )
    parser.add_argument(
        "--model-origin", nargs=3, type=float, default=None, metavar=("X", "Y", "Z"),
        help="约定局部原点的绝对坐标。省略时，局部模式用 metadata 的 SRSOrigin。不知道高程基准时不填写假值",
    )
    parser.add_argument(
        "--local-offset",
        action="store_true",
        help="FBX 顶点使用相对约定原点的局部坐标（默认是 metadata 的 SRSOrigin，可用 --model-origin 替换）。侧车文件始终保留绝对坐标",
    )
    parser.add_argument(
        "--absolute",
        action="store_true",
        help="与默认相同：FBX 顶点写绝对坐标。和 --local-offset 同时给出时以绝对坐标为准",
    )
    parser.add_argument("--merge", action="store_true", help="额外输出合并后的 tanks_all.fbx")
    parser.add_argument("--no-merge", action="store_true", help="不输出合并 FBX（默认会输出）")
    parser.add_argument("--no-blender", action="store_true", help="只检测并写侧车文件，不调用 Blender")
    parser.add_argument("--no-connectors", action="store_true", help="不检测罐顶之间的走道/管道")
    parser.add_argument("--no-sphere-bracing", action="store_true", help="球罐支腿之间不加 X 形拉杆")
    parser.add_argument(
        "--no-structures", action="store_true",
        help="不检测附属结构（楼梯塔、罐顶平台/走道、罐壁盘梯、球罐基础墩）",
    )
    parser.add_argument(
        "--pipes", action="store_true",
        help="同时建管道：罐壁立管、单独管线、管廊（默认不建，管道另行用激光扫描数据建模）",
    )
    parser.add_argument(
        "--no-grounding", action="store_true",
        help="不做落地校正（默认把罐底、基础墩、楼梯塔、盘梯脚贴到实测地面或基础顶面，并建混凝土基础）",
    )
    parser.add_argument(
        "--exclude", action="append", default=[], metavar="Tank_###[,Tank_###]",
        help="剔除人工核查后不是罐的检测结果（如树），按完整运行时的编号；其余罐、走道保持原编号。可重复",
    )
    parser.add_argument(
        "--no-tree-filter", action="store_true",
        help="关闭树冠过滤。默认开启：纹理以植被绿色为主且罐壁/罐顶几何不规整的圆柱判为树冠并剔除，"
             "理由写入 tree_filter_report.csv（完全离线，不用任何在线服务）",
    )
    parser.add_argument(
        "--junction-rule", default="none", choices=("none", "seat_to_measured_roof"),
        help="桥端修正规则，默认 none：端点用罐壁外实测桥面，不贴屋面。"
             "seat_to_measured_roof 与校正文件相同：只贴桥头扇区屋面，不贴整圈中位；"
             "贴合会使实测桥坡变化超过 4° 时拒绝并保持桥面，结果写入 seat_report.json",
    )
    parser.add_argument(
        "--corrections", default=None,
        help="人工校正 JSON。只采用 source 为 manual 或 assumed 且写了数值的条目；"
             "空值、unmeasured 和不写 source 的条目不建护栏、不移动桥端、不加柱",
    )
    parser.add_argument("--limit-tiles", type=int, default=None, help="每个块最多读取的最细瓦片数（调试用）")
    parser.add_argument("--self-test", action="store_true", help="生成合成场景并跑通全流程，用于没有真实数据时的自检")
    parser.add_argument("--version", action="version", version=f"tankextract {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test(args)

    if not args.input or not args.output:
        parser.error("请提供 --input 和 --output，或使用 --self-test")

    options = _options_from_args(args)
    try:
        tanks = run(Path(args.input), Path(args.output), options)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    _print_summary(tanks)
    return 0


def _load_corrections(path: str | None):
    if not path:
        return None
    from tankextract.corrections import load_corrections

    return load_corrections(Path(path))


def _options_from_args(args) -> RunOptions:
    return RunOptions(
        reader=args.reader,
        osgconv=args.osgconv,
        blender=args.blender,
        lod=str(args.lod),
        min_radius=args.min_radius,
        max_radius=args.max_radius,
        min_height=args.min_height,
        max_height=args.max_height,
        max_rms=args.max_rms,
        min_coverage=args.min_coverage,
        cell_size=args.cell_size,
        max_base_above_ground=args.max_base_above_ground,
        max_aspect=args.max_aspect,
        local_offset=bool(args.local_offset) and not args.absolute,
        merge=bool(args.merge or not args.no_merge),
        segments=args.segments,
        precision_m=args.precision,
        max_faces=args.max_faces,
        quality=args.quality,
        tube_sides=args.tube_sides,
        name_prefix=args.name_prefix,
        material_name=args.material,
        model_origin=tuple(args.model_origin) if args.model_origin else None,
        no_blender=args.no_blender,
        limit_tiles=args.limit_tiles,
        connectors=not args.no_connectors,
        sphere_bracing=not args.no_sphere_bracing,
        structures=not args.no_structures,
        pipes=args.pipes,
        grounding=not args.no_grounding,
        tree_filter=not args.no_tree_filter,
        exclude=tuple(n for item in args.exclude for n in item.split(",") if n.strip()),
        corrections=_load_corrections(args.corrections),
        junction_rule=args.junction_rule,
    )


def _self_test(args) -> int:
    from tankextract.synthetic import ORIGIN, generate_scene

    output = Path(args.output or "tankextract-selftest")
    scene = output / "synthetic_osgb"
    block = generate_scene(scene)
    print(f"已生成合成块: {block}")
    print(f"osgconv: {library_hint()}")
    print(f"Blender: {resolve_blender(args.blender) or '未找到'}")
    options = _options_from_args(args)
    if args.blender is None and resolve_blender(None) is None:
        # Self-test still checks detection when Blender is absent.
        options.no_blender = True
    try:
        tanks = run(block, output / "tanks", options)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    _print_summary(tanks)
    print(f"绝对坐标原点: {ORIGIN[0]}, {ORIGIN[1]}, {ORIGIN[2]}")
    print(f"结果目录: {output / 'tanks'}")
    return 0


def _print_summary(tanks) -> None:
    if not tanks:
        print("没有检测到圆罐。可放宽 --min-radius / --min-height / --max-rms。")
        return
    print(f"{'名称':<12} {'X':>14} {'Y':>14} {'半径':>8} {'壁高':>8} {'总高':>8} {'顶':>6} {'RMS':>8}")
    for tank in tanks:
        print(
            f"{tank.name:<12} {tank.center_x:14.3f} {tank.center_y:14.3f} "
            f"{tank.radius:8.3f} {tank.wall_height:8.3f} {tank.total_height:8.3f} "
            f"{tank.roof_type:>6} {tank.rms:8.4f}"
        )


if __name__ == "__main__":
    sys.exit(main())
