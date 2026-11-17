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
    parser.add_argument("--segments", type=int, default=96, help="重建圆周分段数，默认 96")
    parser.add_argument(
        "--local-offset",
        action="store_true",
        help="FBX 顶点使用相对 SRSOrigin 的局部坐标（避免大坐标的 float32 精度损失）。侧车文件始终写绝对坐标",
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
        local_offset=args.local_offset,
        merge=bool(args.merge or not args.no_merge),
        segments=args.segments,
        no_blender=args.no_blender,
        limit_tiles=args.limit_tiles,
        connectors=not args.no_connectors,
        sphere_bracing=not args.no_sphere_bracing,
        structures=not args.no_structures,
        pipes=args.pipes,
        grounding=not args.no_grounding,
        exclude=tuple(n for item in args.exclude for n in item.split(",") if n.strip()),
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
