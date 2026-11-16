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

# 2. conda-forge 的 OpenSceneGraph 3.6.5 + Python 3.11 + numpy
"$HOME/3dobj_env/bin/micromamba" create -y -p "$HOME/3dobj_env/osg" -c conda-forge \
  python=3.11 numpy scipy "openscenegraph=3.6.5"

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

`python -m tankextract --help` 的说明是中文。

## 输出

- `Tank_001.fbx`、`Tank_002.fbx`…：白模，单位米，Z 轴向上，无贴图。对象名如 `Tank_001_Wall`、`_Roof`、`_Rim`、`_Girder_01`、`_BaseRing`、`_Stair`。
- `tanks_all.fbx`：全部罐子合在一个文件里。
- `tanks.geojson`、`tanks.csv`：每个罐的绝对坐标和尺寸。CSV 带 UTF-8 BOM，方便用 Excel 打开。
- `tanks.prj`：SRS 为 `EPSG:4534`–`EPSG:4554`（CGCS2000 三度带）时写 WKT，例如 EPSG:4548 为中央子午线 117°E。其它 SRS 原样写入。
- `srs_origin.json`、`tanks_spec.json`：原点、坐标系，以及交给 Blender 的参数。

侧车里的 X、Y、Z 始终是绝对坐标（局部坐标 + SRSOrigin），和 FBX 用的是局部还是绝对无关。

FBX 自定义属性（字符串，避免再被截成单精度）：`epsg`、`srs_origin_x/y/z`、`coordinate_mode`、`radius_m`、`wall_height_m`、`roof_type`、`rms_m`。

CSV / GeoJSON 字段包括：id、X、Y、Z_base、Z_ground、radius_m、wall_height_m、total_height_m、roof_type（`flat` 平顶 / `cone` 锥顶 / `dome` 穹顶）、roof_rise_m、rms_m、coverage_deg，以及顶圈、抗风圈、底圈、螺旋梯（能稳定看出来时）。

## 检测在做什么

1. 只读最细一层。竖直面（法线接近水平）的顶点按 0.25 m 在平面上分箱，所以跨瓦片的罐会落到同一簇。
2. 相邻的竖直面分箱连成簇，每簇做一次圆拟合（竖直跨度当权重，再丢掉半径方向的离群点）。真实厂区里树、栏杆、管廊、地面杂物的竖直面会把几乎整个场景连成一个大簇，所以还会只保留竖直跨度 ≥1、2、3、5、8 m 的分箱分别再连簇一次；同一个罐在几层里都找到时取拟合最好的一次。
3. 一个簇拟合不成一个圆时（几个罐被走道、管廊、楼梯连在一起），用 RANSAC 在簇里逐个找圆，每找到一个就把它从簇里拿掉再找下一个。
4. 第二遍只重读和候选罐相交的瓦片，在筒壁半径附近收紧拟合，得到圆心、半径、壁高、总高和 RMS。然后剔除：罐底高出周围地面 3 m 以上的（楼顶风机筒等），壁高超过 4 倍直径的（烟囱、塔器），圆柱内部中段有大量网格的（工艺装置里的假圆，真罐是封闭壳体），以及筒壁在高度方向不连续的。地面高程取罐外一圈地面的低值，不再误取罐顶。
5. 罐顶用半径–高程的直线 / 二次曲线区分平顶、锥顶、穹顶，再按径向中值剖面复核锥顶 / 穹顶（半径一半处，锥顶约降到矢高的一半，穹顶仍在约 3/4）。顶圈要伸出筒壁才记录；抗风圈是筒壁外侧、沿圆周一圈的窄环；底圈在底部；螺旋梯是筒外一条随高度转动的窄带。

合成场景上半径和高程的误差大约是几毫米到两厘米，RMS 约 6–18 mm。详见 `validation_report.md`。

每次运行还会写 `candidates.csv`：所有候选圆柱、保留与否和原因（rms、coverage、height、elevated base、slender、内部有几何、筒壁不连续），以及屋顶径向剖面，方便核对漏检和误检。

## 已知限制

- 只找竖直圆筒。倾斜罐、卧罐、方罐不会被收成圆柱。
- 圆周盖不住约 260° 的（贴着建筑、缺了一大面）会被丢掉。圆筒形的塔或圆楼会被当成罐。
- 螺旋梯、抗风圈、底圈、顶圈只有在几何上比较完整时才记录。照片模型上的栏杆、细管道通常不稳定，不会硬猜。
- 内浮顶、保温层这些从外壳上看不见的结构不会出现。
- 绝对坐标的 FBX 顶点是 Blender 的 float32。EPSG:4548 的 Y 大约在 3.5×10⁶，float32 的步长约 0.25 m，厘米级尺寸保不住。要可编辑的白模请加 `--local-offset`；测量值以 geojson/csv 的双精度绝对坐标为准。
- 内置解析器不覆盖 OSG 的全部类。没有 binary bracket 的文件若出现未实现的类，该瓦片会失败并尝试 osgconv。inline schema（attribute bit 0x2）直接拒绝。
- 一个 zip 视为一个块、一个原点。跨两个块的罐不会被拼成一个。
- 球罐不会被收成圆柱。半径小于 2 m 的立式小容器默认不收（`--min-radius 1.5` 可放开，但会混进立管、阀门井等）。
- 带高穹顶、外有平台管线的罐，照片模型的筒壁本身有 ±0.2 m 的起伏，半径和壁高的不确定度相应变大。

## 测试

```bash
python -m pytest tests
```

`tests/fixtures/` 里是两个很小的公开 OSGB，只用于确认解析器能读完真实文件。合成场景在测试里生成，不需要外网。
