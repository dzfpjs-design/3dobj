# 倾斜摄影 OSGB 圆罐提取与白模重建

从 Bentley ContextCapture 风格的 OSGB 块里找出竖直圆柱罐（油罐、水罐一类），在 Blender 中建成无纹理白模，并按 `metadata.xml` 里的坐标系导出 FBX。坐标不写死：SRS 与原点都从 `metadata.xml` 读取。

适用于已经解压的块目录、单个 `.zip`，以及里面放了多块（多个文件夹或 zip）的根目录。瓦片按文件流式读取，不会把几十 GB 一次装进内存。

## 安装（macOS，Apple Silicon，无需 sudo、无需 Homebrew）

OpenSceneGraph 3.6.5 用 micromamba 装到家目录即可。若 `~/3dobj_env/osg` 已经存在，跳过创建环境这一步。

```bash
# 1. micromamba 本体（只是一个可执行文件，放在家目录）
mkdir -p "$HOME/3dobj_env"
curl -Ls https://micro.mamba.pm/api/micromamba/osx-arm64/latest \
  | tar -C "$HOME/3dobj_env" -xvj bin/micromamba

# 2. conda-forge 的 OpenSceneGraph 3.6.5 + Python 3.11 + numpy + scipy + pillow
#    （树冠过滤用 Pillow 解码瓦片里的 JPEG；已有环境执行下面的 pip install 也会装上）
"$HOME/3dobj_env/bin/micromamba" create -y -p "$HOME/3dobj_env/osg" -c conda-forge \
  python=3.11 numpy scipy pillow "openscenegraph=3.6.5"

# 3. 本工具（装进同一个环境，不需要系统 pip）
cd /path/to/3dobj
"$HOME/3dobj_env/osg/bin/python" -m pip install -e .
```

装完后这两个文件应存在：

- `~/3dobj_env/osg/bin/osgconv`
- `~/3dobj_env/osg/bin/python`

确认 osgconv 能读真实瓦片（输出的 `v` 是相对 SRSOrigin 的米，不是加上原点之后的大数）：

```bash
"$HOME/3dobj_env/osg/bin/osgconv" \
  "$HOME/3dobj_data/E_OSGB/Data/Tile_+000_+009/Tile_+000_+009_L19_0uuuu0.osgb" \
  /tmp/tile.obj
head /tmp/tile.obj
```

Blender 4.x 使用已经装好的应用程序，不需要放进 PATH：

`/Applications/Blender.app/Contents/MacOS/Blender`

Linux 上同样可以用 micromamba（把上面的 `osx-arm64` 换成 `linux-64`）。没有 osgconv 时，内置解析器仍然可以读 ContextCapture 的 OSGB。

## Windows 安装与使用说明

面向一台干净的 Windows 10/11 64 位电脑：不需要预先安装 Python、conda 或 Blender。安装包里已经带上 Python 运行时、numpy、scipy、Pillow，以及便携版 Blender 4.2.14 LTS。EPSG:4548 的投影字符串写在程序里，不依赖 pyproj。OSGB 用内置解析器读取，不附带 osgconv（Mac 上可选的那条路径在 Windows 安装包里没有）。

1. 从本仓库的 GitHub Release（预发布 `tankextract-v1.0.0`）或 Actions 的 `TankExtract-Setup` 附件下载 `TankExtract-Setup-1.0.0.exe`。
2. 双击安装。可以改安装目录。开始菜单会有「罐体提取」和「卸载罐体提取」。
3. 打开开始菜单里的「罐体提取」（安装目录中的 `TankExtractGUI.exe`）。选择 OSGB 块文件夹或 zip（中文路径可以，例如 `D:\来安模型\E_OSGB`），再选输出文件夹。建议勾选「局部坐标输出」。排除罐编号填 `Tank_001,Tank_059` 这种形式。点「开始提取」，日志显示在窗口下方。
4. 命令行是同一个目录里的 `tankextract.exe`（默认安装目录是 `C:\Program Files\TankExtract`）：

```bat
"C:\Program Files\TankExtract\tankextract.exe" ^
  --input "D:\来安模型\E_OSGB" ^
  --output "D:\来安模型\输出" ^
  --local-offset
```

程序按这个顺序找 Blender：`--blender`、环境变量 `BLENDER`、安装目录里的 `blender\blender.exe`、PATH、最后才是本机「Program Files\Blender Foundation」里已安装的 Blender。装好之后不需要联网。

卸载用开始菜单里的「卸载罐体提取」。

## OSGB 怎么读

默认 `--reader auto`：

1. 用内置 Python 解析器读。它按 OpenSceneGraph 的二进制序列化实现，覆盖倾斜摄影里常见的 SO 版本（大约 77–160），包括 ContextCapture 常见的 91（无 binary bracket、数组内联）和带 bracket 的 131。纹理整段跳过，只留三角网。已在公开的大雁塔 OSGB（91）和 GeoRefine 样例（131）上核对到文件最后一个字节。
2. 某一瓦片解析失败，并且能找到 `osgconv` 时，自动改用 osgconv 把该瓦片转成 OBJ 再读。

`--reader osgconv` 则全部走 osgconv。这就是在 Mac 上已经验证过的读法。程序按下面的顺序找可执行文件，一般不用再写路径：

1. `--osgconv`
2. 环境变量 `OSGCONV`
3. `PATH` 里的 `osgconv`
4. `~/3dobj_env/osg/bin/osgconv`

osgconv 不会跟着 PagedLOD 去加载子文件。最细一层靠文件名：每个瓦片目录里 `_L层号_` 最大的那些文件。内置解析器还会再看 PagedLOD 里的子文件名，若磁盘上还有更细的文件就继续往下读。

选内置解析器做默认，是因为一块数据往往有几千到上万个瓦片，逐个启动 osgconv 并落盘 OBJ 会慢很多。两种读法对同一网格得到的顶点一致（见 `validation_report.md`）。遇到内置解析器报「不支持的类」时，加上 `--reader osgconv` 即可。

## 用法

在已解压的 E_OSGB 上（推荐建模用局部坐标，测量值仍写绝对坐标）：

```bash
"$HOME/3dobj_env/osg/bin/python" -m tankextract \
  --input "$HOME/3dobj_data/E_OSGB" \
  --output "$HOME/3dobj_data/E_OSGB_tanks" \
  --reader osgconv \
  --local-offset \
  --blender "/Applications/Blender.app/Contents/MacOS/Blender"
```

Blender 和 osgconv 都能自动找到的话，可以更短：

```bash
"$HOME/3dobj_env/osg/bin/python" -m tankextract \
  --input "$HOME/3dobj_data/E_OSGB" \
  --output "$HOME/3dobj_data/E_OSGB_tanks" \
  --local-offset
```

zip，或一个目录里有多块 zip：

```bash
"$HOME/3dobj_env/osg/bin/python" -m tankextract \
  --input "$HOME/Downloads/来安模型（合并后）/A1_South/A1_OSGB/FG_osgb.zip" \
  --output "$HOME/Downloads/FG_tanks" \
  --local-offset
```

没有真实数据时，生成合成场景并跑通全流程：

```bash
python -m tankextract --self-test --output /tmp/tank-selftest --no-blender
```

### 参数

| 参数 | 含义 | 默认 |
| --- | --- | --- |
| `--input` / `-i` | 块目录、zip，或含多块的根目录 |  |
| `--output` / `-o` | 输出目录 |  |
| `--reader` | `auto`、`python`、`osgconv` | `auto` |
| `--osgconv` | osgconv 路径 | PATH，否则 `~/3dobj_env/osg/bin/osgconv` |
| `--blender` | Blender 可执行文件 | PATH，否则 `/Applications/Blender.app/Contents/MacOS/Blender` |
| `--lod` | `finest` 或层号（如 `21`） | `finest` |
| `--min-radius` / `--max-radius` | 半径范围（米） | 2.0 / 80 |
| `--min-height` / `--max-height` | 壁高范围（米） | 2 / 80 |
| `--max-rms` | 圆拟合 RMS 上限（米） | 0.20 |
| `--min-coverage` | 圆周至少覆盖的角度 | 260 |
| `--cell-size` | 水平分箱（米） | 0.25 |
| `--max-base-above-ground` | 罐底高出周围地面的上限（米），超过视为楼顶设备 | 3 |
| `--max-aspect` | 壁高/直径上限，超过视为烟囱、塔器 | 4 |
| `--segments` | 白模圆周分段 | 96 |
| `--local-offset` | FBX 用局部坐标 | 关闭（顶点写绝对坐标） |
| `--merge` / `--no-merge` | 是否另存 `tanks_all.fbx` | 保存 |
| `--no-blender` | 只检测、不导出 FBX | 关闭 |
| `--limit-tiles` | 每块最多读多少个最细瓦片 | 不限 |
| `--no-connectors` | 不找罐间走道 | 关闭 |
| `--no-sphere-bracing` | 球罐支腿间不加 X 拉杆 | 关闭 |
| `--no-structures` | 不检测附属结构（楼梯塔、罐顶平台 / 走道、罐壁盘梯、球罐基础墩） | 关闭 |
| `--pipes` | 同时建管道：罐壁立管 `Tank_###_pipe_##`、单独管线 `Pipe_###`、管廊 `PipeRack_###`、罐顶之间无护栏的管道连接。默认不建（管道另用激光扫描数据建模） | 关闭 |

| `--no-grounding` | 不做落地校正（见下文“落地校正”） | 关闭 |

`python -m tankextract --help` 的说明是中文。

## 输出

- `Tank_001.fbx`、`Tank_002.fbx`…：白模，单位米，Z 轴向上，无贴图。每个罐是一个父级空物体 `Tank_001`（放在罐底圆心，真实坐标），罐体和每个附属结构都是它下面独立的网格对象，可以单独选中、移动、删除或修改：

  | 对象名 | 内容 |
  |---|---|
  | `Tank_001` | 父级空物体（Empty），位置 = 罐底中心 |
  | `Tank_001_body` | 罐体：底板 + 筒壁 + 罐顶（平顶 / 锥顶 / 拱顶）为一个封闭的流形实体（共用边界顶点、法线朝外）；球罐为整个封闭球壳 |
  | `Tank_001_handrail` | 罐顶周边护栏 |
  | `Tank_001_top_rim` | 顶圈 |
  | `Tank_001_wind_girder_01`… | 抗风圈 |
  | `Tank_001_base_ring` | 底圈（罐壁底部、高出罐底的钢圈） |
  | `Tank_001_foundation` | 罐下混凝土基础（圆柱，从实测地面到基础顶面，半径 = 罐半径 + 实测外伸；罐体落在它顶面上） |
  | `Tank_001_stair` | 螺旋梯 |
  | `Tank_001_leg_01`… | 球罐支腿（每根一个对象） |
  | `Tank_001_brace_01a` / `_01b`… | 球罐支腿间 X 形拉杆（每根一个对象，上下端高程实测） |
  | `Tank_001_pedestal_01`… | 球罐支腿下的混凝土基础墩（实测高和平面尺寸） |
  | `Tank_001_stair_treads` / `_stair_stringer_inner` / `_stair_stringer_outer` / `_stair_rail` / `_stair_rail_posts` / `_stair_landing` | 罐壁盘梯：踏步、内外侧梁、外侧扶手和中横杆、立柱、顶部平台（实测起止方位、高程、坡度、梯宽） |
  | `Tank_001_platform` / `_platform_rail_top` / `_platform_rail_mid` / `_platform_rail_posts` | 罐顶中心平台和护栏（球罐顶平台、拱顶罐中心平台） |
  | `Tank_001_roof_walkway_01` / `_roof_walkway_01_rail_L_top`… | 罐顶径向走道（从罐边盘梯口到中心平台，贴罐顶面）和两侧护栏 |
  | `Tank_001_pipe_01`… | 沿罐壁竖直下行的立管，实测直径（仅 `--pipes`） |

- 独立附属结构，每个一个父级空物体，部件是它下面的独立网格对象（都写在 `structures.fbx`，也合进 `tanks_all.fbx`）：

  | 父级 | 部件 |
  |---|---|
  | `Stair_001`（楼梯塔，位置 = 塔底中心） | `_column_01`…`_04`（角柱）、`_beam_01`…（每层圈梁）、`_brace`（侧面斜撑）、`_flight_01`…（每跑梯段：踏步 + 两侧梁）、`_rail_flight_01_top/_mid/_posts`（梯段扶手）、`_landing_01`…（休息平台）、`_top_deck`、`_rail_top_top/_mid/_posts`（塔顶护栏，朝走道一侧开口）、`_bridge_01`（塔顶到走道的过桥板，有缝时） |
  | `PipeRack_001`（管廊，仅 `--pipes`，位置 = 起点中线地面） | `_column_01A/B`…（每榀两根立柱）、`_crossbeam_t1`…（每层横梁）、`_longbeam_t1A/B`…（纵梁）、`_pipe_01`…（每根管道一个对象，实测直径和高程） |
  | `Pipe_001`（单独的管道 / 管线，仅 `--pipes`，位置 = 起点地面） | `_pipe_01`、`_support_01`… |

- `tanks_all.fbx`：全部罐子（同样的层级）加全部罐间连接和独立附属结构，合在一个文件里。
- `connectors.fbx`：只含罐间连接。每处连接一个父级空物体 `Connector_001`（放在走道起点），下面是 `Connector_001_deck`（走道板）、`_rail_L_top` / `_rail_L_mid` / `_rail_R_top` / `_rail_R_mid`（两侧扶手和中横杆）、`_post_L_01`…（护栏立柱）、`_support_01`…（落地支撑）；被识别为楼梯塔的支撑不在这里重复，而是单独的 `Stair_###`。无护栏的窄连接按管道建为 `_pipe`。
- `connectors.csv`、`connectors.geojson`：每处连接两端的绝对坐标和高程、长度、坡度、宽、板厚、护栏高（`handrail_source`：`measured` 实测，或 `assumed …` 网格太薄看不到护栏时取全场实测中位数；`rail_sides_seen` 记录看到护栏的是哪一侧）、落地支撑（位置、平面尺寸、底 / 顶高程，楼梯塔时带 `stair_tower` 名称）。GeoJSON 里连接是三维 LineString，支撑是 Point。`connectors_debug.json` 记录每对相邻罐为什么有 / 没有连接。
- `structures.csv`、`structures.geojson`：每个附属结构一行 / 一个要素：楼梯塔（平面框、层高、各层高程）、管廊（轴线、长宽、各层高程、榀数和间距、每根管的直径 / 高程 / 横向位置 / 长度）、单独管道、罐壁立管、罐壁盘梯、罐顶平台、罐顶走道、球罐基础墩。`dims` 列是完整的实测参数（JSON）。`structures_stats.json` 是每块的计数。
- `tanks.geojson`、`tanks.csv`：每个罐的绝对坐标和尺寸。CSV 带 UTF-8 BOM，方便用 Excel 打开。
- `tanks.prj`：SRS 为 `EPSG:4534`–`EPSG:4554`（CGCS2000 三度带）时写 WKT，例如 EPSG:4548 为中央子午线 117°E。其它 SRS 原样写入。
- `srs_origin.json`、`tanks_spec.json`：原点、坐标系，以及交给 Blender 的参数。

侧车里的 X、Y、Z 始终是绝对坐标（局部坐标 + SRSOrigin），和 FBX 用的是局部还是绝对无关。

FBX 自定义属性（字符串，避免再被截成单精度）：`epsg`、`srs_origin_x/y/z`、`coordinate_mode`、`radius_m`、`wall_height_m`、`roof_type`、`rms_m`。

CSV / GeoJSON 字段包括：id、X、Y、Z_base、Z_ground、radius_m、wall_height_m、total_height_m、roof_type（`flat` 平顶 / `cone` 锥顶 / `dome` 球冠拱顶 / `sphere` 球罐）、roof_rise_m、rms_m、coverage_deg，以及顶圈、抗风圈、底圈、螺旋梯（能稳定看出来时），handrail_height_m（护栏高）、sphere_center_z、sphere_leg_count、sphere_leg_diameter_m、sphere_leg_rho_m（支腿中心到球心的水平距离）、sphere_leg_height_m、sphere_leg_azimuth_deg（各支腿方位角，正东起逆时针）、sphere_bracing（球罐），以及 sphere_leg_diameter_v5_m（旧版直径，供对比）、sphere_pedestal_height_m / _size_m、sphere_brace_z0 / z1、roof_platform_z / _radius_m / _rail_m、roof_walkways（JSON）、wall_stair_*（起止方位、高程、梯宽、坡度、转角、是否外推）、wall_pipe_count、wall_pipes（JSON）。

## 检测在做什么

1. 只读最细一层。竖直面（法线接近水平）的顶点按 0.25 m 在平面上分箱，所以跨瓦片的罐会落到同一簇。
2. 相邻的竖直面分箱连成簇，每簇做一次圆拟合（竖直跨度当权重，再丢掉半径方向的离群点）。真实厂区里树、栏杆、管廊、地面杂物的竖直面会把几乎整个场景连成一个大簇，所以还会只保留竖直跨度 ≥1、2、3、5、8 m 的分箱分别再连簇一次；同一个罐在几层里都找到时取拟合最好的一次。
3. 一个簇拟合不成一个圆时（几个罐被走道、管廊、楼梯连在一起），用 RANSAC 在簇里逐个找圆，每找到一个就把它从簇里拿掉再找下一个。
4. 第二遍只重读和候选罐相交的瓦片，在筒壁半径附近收紧拟合，得到圆心、半径、壁高、总高和 RMS。然后剔除：罐底高出周围地面 3 m 以上的（楼顶风机筒等），壁高超过 4 倍直径的（烟囱、塔器），圆柱内部中段有大量网格的（工艺装置里的假圆，真罐是封闭壳体），以及筒壁在高度方向不连续的（筒壁完整、覆盖 ≥ 350°、壁面连续率 ≥ 75%、RMS ≤ 7 cm 的干净壳体允许内部有 25% 的杂散网格，照片模型常把罐顶或管子“垂”进筒内）；罐顶以下的筒壁不足 `min_height`、或非平顶且罐顶矢高超过一倍半径的也剔除（泵房上的波纹桶、叠放设备）。地面高程取罐外一圈地面的低值，不再误取罐顶。
5. 罐顶：对朝上的屋面按半径分环取低分位高程（栏杆、走道、中心平台只占每一环的一小部分，不会抬高这条下包络），分别拟合平顶、锥顶（线性）和球冠穹顶（拱顶，按实测矢高求球半径），取残差最小者。小罐（半径 < 4 m）矢高小于 max(0.2 m, 0.12×半径)（约缓于 1:8）按平顶处理，顶上的平台、呼吸阀不再被当成锥顶。罐壁顶部常有一圈约 1.1 m 的护栏，筒壁顶取罐壁内侧最低一环屋面（大罐护栏内侧的环形走道）的实测高程，护栏高度单独记为 `handrail_height_m` 并在白模里做成一圈薄带。
6. 球罐：若筒壁以上的表面大部分落在以筒壁半径为半径的球面上、并接近球顶，判为球罐（`roof_type=sphere`），输出球心高程、球半径和支柱（按支柱处竖直面的方位直方图计数），白模为球体加支柱。顶圈要伸出筒壁才记录；抗风圈是筒壁外侧、沿圆周一圈的窄环；底圈在底部；螺旋梯是筒外一条随高度转动的窄带。

7. 罐间连接：对每一对相邻（罐壁间距 ≤ 30 m、中间没有别的罐）的罐，在两罐圆心连线两侧 ±3 m 的走廊里、罐顶高度附近找连续的朝上面：要覆盖两罐之间至少 75% 的长度。走道板高程用“覆盖站点最多的直线”求（允许坡度，能分开走道板和它上面的管子、下面的楼梯平台），再沿轴线向罐顶延伸到走道板落到罐顶面上为止（球罐的走道一直通到顶部平台）。走道两端都不能比各自罐檐低 0.5 m 以上（只认罐顶到罐顶），罐之间更低处的管廊、侧平台不算。宽度取走道板面的横向范围，板厚取板面到板底朝下面的距离，护栏取两侧边缘 0.3–1.6 m 高的面，落地支撑 / 楼梯塔取走道下方从地面一直连到板底的竖直面。窄而无护栏的按管道（直径 = 宽度）输出。
8. 球罐支腿：按方位直方图找峰，再按等间距校正（支腿总是均布的）。
9. 附属结构（`structures.py`，对所有块通用，不针对某个厂区）：
   - 楼梯塔：罐间走道下方的落地支撑若平面 ≥ 1.5 m、高 ≥ 4 m，就在它周围取面片：竖直面的稳健最小外接矩形给出平面尺寸和朝向；框内朝上面的面积直方图峰值就是各层休息平台，按中位层高规整（缺测的层按层高补）。白模为四根角柱、每层圈梁、侧面斜撑、往返梯段（每跑一层，沿长边、各占半宽）、两端休息平台、梯段扶手、塔顶平台和护栏（朝走道一侧开口，有缝时加过桥板）。
   - 罐壁盘梯：罐壁外 0.15–1.8 m 的朝上面（踏步），在展开图（弧长 s, z）上做直线 Hough（坡度 19°–51°，两个旋向），取最长的连续段；网格太稀疏只看到一段时沿同一坡度外推到地面或罐顶（`wall_stair_extended` 标出）。
   - 罐壁立管（仅 `--pipes`）：罐壁外 0.05–1.0 m（球罐为支腿圈外）的竖直面，展开图上竖向覆盖 ≥ 55% 的窄列（< 1 m 宽）就是立管；直径用水平切片 RANSAC 圆（要看到 ≥ 210° 的轮廓）取中位数。避开盘梯和支腿所在方位。
   - 罐顶平台和走道：球罐取球顶以上最集中的朝上面高程为平台面，半径取平台面仍覆盖 60% 方位的最大半径；拱顶罐先按半径分环量出罐顶实际剖面，高出罐顶 0.25–1.8 m 的面在中心 2.5 m 内的是中心平台，在某个方位上覆盖大部分半径的是径向走道（方位、偏距、宽度、护栏高实测），走道板贴着实测罐顶剖面。小罐（平台半径 > 0.35 R）不建平台。
   - 球罐基础墩、支腿、拉杆：每根支腿离地 1.6–7 m 每 0.5 m 一层做 RANSAC 圆，直径取中位数（旧版用全部点拟合，被拉杆、管线拉大了约 0.4 m）；支腿脚下朝上面的高程是墩顶，墩下竖直面的径向 / 切向范围是墩的平面尺寸；拉杆在每两根相邻支腿间的竖直面内搜索最好的一对上下端高程（X 形两根对称），十个面一起投票。
   - 管廊和管道（仅 `--pipes`，默认关闭）：按厂区主方向（竖直面法线方位直方图）旋转，地面以上 1.6–14 m、不属于罐 / 楼梯塔 / 走道的面在平面上栅格化，沿两个主方向找 ≥ 8 m 的连续条带。每条带：截面上用面法线做圆 Hough（每个面片按自己的法线给“圆心 = 点 − r·法线”投票，管道互不相交，贪心取票数最高的圆），要求看到 ≥ 150° 的轮廓、覆盖 ≥ 40% 长度；各层高程取朝上面平面覆盖率的峰值；榀位取条带边缘从地面一直连到最低层的竖直面。高于地面 10 m 的不算管廊；两侧立面连续（建筑墙）或屋面满铺且没有管道的按建筑剔除。截面上一根管都看不到的条带（棚架、设备框架）不建。宽 < 1 m 的按单独管道（`Pipe_###`，管子放在单柱支墩上，不建两柱框架）。大罐（平台到罐边 ≥ 3 m）才找径向走道。
合成场景上半径和高程的误差大约是几毫米到两厘米，RMS 约 6–18 mm。详见 `validation_report.md`。

每次运行还会写 `candidates.csv`：所有候选圆柱、保留与否和原因（rms、coverage、height、elevated base、slender、内部有几何、筒壁不连续），以及屋顶径向剖面，方便核对漏检和误检。

## 实际数据的容错

- 在 macOS 上解压或拷贝过的数据常带 AppleDouble 垃圾文件（`._Tile_xxx.osgb`）和 `__MACOSX/`、`.DS_Store`，文件夹和 zip 里都会跳过，不当作瓦片读。
- 个别瓦片本身损坏（例如 BC_osgb_Clip 的 `Tile_+006_+017_L22_00004000t1.osgb` 在源数据里就被截断，zip 校验正常）时，跳过该瓦片并打印“警告”，`read_stats.json` 里 `unreadable` 是跳过的个数，`unreadable_tiles` 列出文件名和原因（最多 200 条）。其余瓦片照常处理。
- 第二遍精拟合只读和候选罐外接框（半径 + 4.2 m）相交的瓦片，走道 / 附属结构的取面也先按瓦片外接框筛一遍，几万个瓦片的块（A_OSGB_cip 约 5.7 万个）也能在 20 分钟左右跑完。

## 剔除人工核查出的误检

**树冠过滤（默认开启，完全离线）**：圆形的树冠有时会通过圆柱检测。工具直接读取 OSGB 瓦片内嵌的纹理，计算罐体范围内植被绿色（色相 55–180°、饱和、超绿 2G−R−B 高）所占比例；同时看三项几何不规整：罐壁圆拟合 RMS ≥ 0.05 m、罐壁高度覆盖 < 0.85、罐顶平/锥/拱拟合 RMS ≥ 0.06 m。绿色占比 ≥ 60% **且** 至少两项几何不规整才判为树冠剔除，所以涂成绿色但几何规整的罐会保留。剔除发生在编号之后，其余罐保持原编号；每个罐的颜色、几何指标和判定理由写入 `tree_filter_report.csv`，`candidates.csv` 中标为 `tree crown`。读不到纹理（如 osgconv 读取）时不剔除，只在报告中标“疑似树冠，请人工核对”。用 `--no-tree-filter` 关闭。来安数据上：8 棵树绿色占比 73–99% 全部剔除，357 个罐全部保留（BC Tank_064 绿色 76%，几何规整）。

仍有漏网时，检测结果里偶尔有树冠之类被当成小罐。在正射影像上核查后，可用 `--exclude Tank_001,Tank_059`（可重复）重跑同一个块：编号按完整运行时的编号给，被剔除的罐连同基础、盘梯、顶部平台等附属件都不再生成，与它相连的走道也去掉；其余罐和走道保持原编号，便于追溯。编号不存在时报错。

## 已知限制

- 只找竖直圆筒。倾斜罐、卧罐、方罐不会被收成圆柱。
- 圆周盖不住约 260° 的（贴着建筑、缺了一大面）会被丢掉。圆筒形的塔或圆楼会被当成罐。
- 螺旋梯、抗风圈、底圈、顶圈只有在几何上比较完整时才记录。照片模型上的栏杆、细管道通常不稳定，不会硬猜。
- 内浮顶、保温层这些从外壳上看不见的结构不会出现。
- 绝对坐标的 FBX 顶点是 Blender 的 float32。EPSG:4548 的 Y 大约在 3.5×10⁶，float32 的步长约 0.25 m，厘米级尺寸保不住。要可编辑的白模请加 `--local-offset`；测量值以 geojson/csv 的双精度绝对坐标为准。
- 内置解析器不覆盖 OSG 的全部类。没有 binary bracket 的文件若出现未实现的类，该瓦片会失败并尝试 osgconv。inline schema（attribute bit 0x2）直接拒绝。Windows 安装包不带 osgconv，这种瓦片会被跳过并记入 `read_stats.json`。
- Windows 安装包体积大，主要是内置的 Blender 4.2.14。FBX 导出只用这一版测试过的脚本接口。
- 一个 zip 视为一个块、一个原点。跨两个块的罐不会被拼成一个。
- 球罐按上半球拟合；下半球常被照片模型糊成竖直“裙边”，不参与球半径拟合。半径小于 2 m 的立式小容器默认不收（`--min-radius 1.5` 可放开，但会混进立管、阀门井等）。
- 罐间连接只找“罐顶到罐顶”的走道 / 管道。罐到建筑的走道不建模。
- 楼梯塔的梯段布置（往返、每层一跑、沿长边）和侧面斜撑形式是按常规钢梯塔给的，层高、平台高程、平面尺寸是实测的；照片模型分辨不出单根踏步和斜撑方向。
- 管廊：管道是直的、和管廊等长的一段（按实测起止位置），弯头、立管接驳、阀门不建；截面上被遮挡的管、贴在一起的小管可能漏掉或合成一根；角撑（斜撑）不建；榀位看不清的管廊按 6 m 等距（`bents_source` 标出）。
- 不在管廊上、自由走向的管线（例如球罐顶部沿球面下行再拐到管廊的管子）不追踪，只建竖直段（`Tank_###_pipe_##`）。
- 球罐的水喷淋环管、罐顶小附件（呼吸阀、人孔、量油孔）不建模。
- 带高穹顶、外有平台管线的罐，照片模型的筒壁本身有 ±0.2 m 的起伏，半径和壁高的不确定度相应变大。

## 落地校正（v8）

每个落地的对象都和 OSGB 实测地面比一次底高程，超出容差（5 cm）的贴回去：

- 地面 = 对象外一圈朝上面片高程的稳健低端（面积加权 25% 分位附近 ±0.12 m 的中位数），所以路缘、踏步、设备、地上管道抬不高它。
- 立式罐：先量罐壁外 0.6–3 m 的地面 g0；再在罐壁 −0.25…+0.35 m 环带里找高出 g0 的朝上台阶（面积加权高程直方图的峰，从低往高），方位覆盖 ≥ 40%、台阶下方的竖直侧面填满 ≥ 60% 高度时判为混凝土基础，外伸量按 0.1 m 一环往外长；最终地面取基础外沿 0.4–2.9 m。罐底在基础顶面 1 m 以内时落在基础顶面上，否则落在地面上。罐壁顶（檐口）不动，只改罐底，所以 `wall_height_m` / `total_height_m` 跟着变。基础另建一个对象 `Tank_###_foundation`。
- 球罐：每根支腿脚外 1.1–2.6 m 单独量地面，每个基础墩从自己的地面建到墩顶；支腿落在墩顶。
- 楼梯塔、走道支撑：矩形外 0.4–2.0 m 的地面。
- 罐壁盘梯：梯脚 1.8 m 以内、罐壁和基础以外的地面，−1.0 m < 差值 < 1.2 m 时把梯脚贴地。

每次运行写 `ground_report.csv`：object、kind、parent、stands_on（ground / foundation / pedestal top）、ground_z、support_top_z、bottom_before_z、gap_to_ground_before_m、gap_before_m（相对支撑面，正 = 飘空、负 = 陷入）、bottom_after_z、gap_after_m、note。`tanks.csv` / GeoJSON 增加 Z_base_measured、ground_gap_m、ground_gap_before_m、foundation、foundation_top_z、foundation_height_m、foundation_radius_m；`structures.csv` 增加 ground_gap_m、ground_gap_before_m。

`--local-offset` 时 FBX 里的高程 = 绝对高程 − 原点 Z（CSV / GeoJSON 里一直是绝对高程）；测试里用原点 Z = 100 检查罐底、基础上下面都对得上。

## 检查白模是否封闭

```bash
/Applications/Blender.app/Contents/MacOS/Blender -b -P tankextract/blender/check_bodies.py -- 输出目录/tanks_all.fbx bodies.json [--all]
```

逐个导入 `Tank_###_body`，检查是否为一个封闭流形（每条边恰好两个面、无边界边、无非流形顶点）、有向体积为正（法线朝外），并给出世界坐标下的半径、底 / 顶高程，用来和 `tanks.csv` 对照。`--all` 检查所有网格对象（基础、基础墩、楼梯塔等），可以把 FBX 底高程 + 原点 Z 和 `ground_report.csv` 对照。

## 测试

```bash
python -m pytest tests
```

`tests/fixtures/` 里是两个很小的公开 OSGB，只用于确认解析器能读完真实文件。合成场景在测试里生成，不需要外网。
