# 隧道限界复核 API

纯后端 JSON API：输入**激光测量导出的隧道断面折线**与**车辆限界多边形**（坐标均为毫米整数），
计算两组线段之间的全局最小欧氏距离，判定车辆限界是否满足指定净距，并定位**唯一的最危险线段对**。
支持单点复核与**批量平移位置复核**（同一断面上一至五十个摆放位置一次核验）。
另支持**同一断面两期测点比对**：以共同基准点排除仪器整体平移后，逐点计算位移并判定是否超过容差；
以及**两期限界影响复核**：把两期测点序列分别作为不闭合折线复核净距，给出净距变化与是否由合格转为不合格。
全程不依赖 CAD 软件，也不依赖任何第三方几何库——线段相交、点到线段距离均为自行实现。

- Python 3.12 · FastAPI · Pydantic v2
- 内部计算使用双精度浮点（IEEE-754 double）
- 响应距离统一**四舍五入到小数点后三位**（`ROUND_HALF_UP`）

## 目录结构

```
app/
  geometry.py     # 线段相交 / 点到线段距离 / 两组线段全局最小距离（纯 Python）
  comparison.py   # 两期测点比对：基准点对齐 + 逐点位移与容差判定（纯 Python）
  schemas.py      # Pydantic 模型与字段级几何校验
  main.py         # FastAPI 入口
scripts/
  acceptance.py   # 一次性验收脚本（仅标准库）
tests/            # pytest：几何边界与 API 行为
Dockerfile
docker-compose.yml
```

## 快速开始

### Docker Compose（推荐）

```bash
# 构建并启动 API + 一次性验收服务
docker compose up --build
```

- `api` 服务监听宿主端口，默认 `8000`，可用环境变量 `API_PORT` 覆盖：

  ```bash
  API_PORT=9001 docker compose up --build
  # API: http://localhost:9001
  ```

- `verify` 是**一次性验收服务**：等待 `api` 健康检查通过后，对其执行端到端断言
  （通过 / 不通过 / 相交 / 闭合边 / 四舍五入 / 字段级错误 / 自交多边形 /
  批量位置全过 / 首个失败不中断 / 平移越界定位 / 旧接口响应不变 /
  断面比对纯平移全过 / 单点位移定位 / 并列选择稳定 / 名称不匹配拒绝 /
  限界影响纯平移不变 / 局部变形失格 / 无效折线拒绝），
  打印 `[PASS]`/`[FAIL]` 后退出，退出码即验收结论（0 通过）。可单独运行：

  ```bash
  docker compose build
  docker compose run --rm verify
  ```

  镜像只由 `api` 服务构建一次（`verify` 不配置 `build`，避免并发构建争抢同名标签）；
  `verify` 以 `pull_policy: never` 强制复用本地镜像，因此单独运行前必须先执行
  `docker compose build`（`up --build` 会自动先构建）。

### 本地开发

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
pytest                 # 106 项测试
BASE_URL=http://127.0.0.1:8000 python scripts/acceptance.py
```

## 接口

### `POST /api/clearance/check`

请求体（所有坐标与净距均为**毫米整数**，绝对值不超过 1,000,000）：

| 字段 | 说明 |
| --- | --- |
| `tunnel_polyline.points` | 隧道断面折线点，**按顺序连接但不闭合**，至少 2 个点 |
| `vehicle_polygon.points` | 车辆限界多边形顶点，**首尾隐式闭合**，至少 3 个点，禁止把首点重复为末点 |
| `required_clearance` | 要求净距（毫米整数） |

点的结构为 `{"x": <int>, "y": <int>}`。

校验规则（违反返回 `422`，错误**定位到具体字段**，多个问题一次性返回）：

- 坐标必须是整数且 `|x|,|y| <= 1,000,000`，净距 `|required_clearance| <= 1,000,000`；
- 同一形状内相邻点不得相同（错误定位到重复点的下标）；
- 多边形不得把首点重复为末点（首尾隐式闭合）；
- 多边形有效顶点不得全部共线形成零面积轮廓（隐式闭合边会与其它边重叠）；
- 多边形不得自交（含非相邻边接触），错误信息给出自交的两条边索引；
- 禁止多余字段。

### 毫米坐标示例

门形隧道断面（开口向下，折线不闭合）与一个 600×800 的矩形车辆限界，四周净距 200mm：

```bash
curl -s http://localhost:8000/api/clearance/check \
  -H 'Content-Type: application/json' \
  -d '{
    "tunnel_polyline": {
      "points": [
        {"x": 0,    "y": 0},
        {"x": 0,    "y": 1200},
        {"x": 1000, "y": 1200},
        {"x": 1000, "y": 0}
      ]
    },
    "vehicle_polygon": {
      "points": [
        {"x": 200, "y": 200},
        {"x": 800, "y": 200},
        {"x": 800, "y": 1000},
        {"x": 200, "y": 1000}
      ]
    },
    "required_clearance": 200
  }'
```

响应：

```json
{
  "passed": true,
  "minimum_clearance_mm": 200.0,
  "required_clearance_mm": 200,
  "intersects": false,
  "dangerous_pair": {
    "tunnel_segment": {
      "start_index": 0,
      "start": {"x": 0, "y": 0},
      "end": {"x": 0, "y": 1200}
    },
    "vehicle_segment": {
      "start_index": 0,
      "start": {"x": 200, "y": 200},
      "end": {"x": 800, "y": 200}
    },
    "distance_mm": 200.0
  }
}
```

字段说明：

- `passed`：仅当两组线段**未相交/未接触**且**未舍入**的全局最小距离 `>= required_clearance` 时为 `true`；
- `minimum_clearance_mm`：全局最小净距，四舍五入到三位小数；
- `intersects`：两组线段是否相交或接触（接触距离按 0 计）；
- `dangerous_pair`：唯一最危险线段对。线段以其**起点在输入点序列中的下标**标识；
  限界多边形的隐式闭合边起点索引为 `n-1`，`end` 自动还原为首点。

字段级错误示例（多边形重复首点）：

```json
{
  "detail": [
    {
      "type": "repeated_first_point",
      "loc": ["body", "vehicle_polygon", "points", 3],
      "msg": "车辆限界多边形末点与首点相同，多边形首尾隐式闭合，禁止重复首点"
    }
  ]
}
```

### `POST /api/clearance/check-series`

批量复核：同一隧道断面与车辆限界下，一次核验 **1~50 个摆放位置**。
请求体在 `check` 的基础上增加 `placements` 列表，其余字段与校验规则完全相同：

| 字段 | 说明 |
| --- | --- |
| `placements[].name` | 位置名称，非空且不能只含空白/控制字符，**同一批次内唯一** |
| `placements[].dx` / `placements[].dy` | 车辆限界整体的横/纵平移量（毫米，**整数**） |

每个位置先把车辆顶点平移 `(dx, dy)`（隧道折线不动），再按与 `check` 完全相同的
最小线段对算法、通过门槛、舍入与并列选边规则计算；危险边端点展示**平移后**的坐标。
**单个位置不合格不会中断批次**，全部位置都会完成计算。

响应按输入顺序给出每个位置的结论（`check` 的响应结构 + `name`）：

```json
{
  "results": [
    {
      "name": "a",
      "passed": true,
      "minimum_clearance_mm": 200.0,
      "required_clearance_mm": 150,
      "intersects": false,
      "dangerous_pair": { "tunnel_segment": {}, "vehicle_segment": {}, "distance_mm": 200.0 }
    }
  ],
  "all_passed": true,
  "first_failed_name": null
}
```

- `all_passed`：全部位置均通过时为 `true`；
- `first_failed_name`：首个不通过位置的名称；全部通过时为 `null`。

批量特有的 `422` 校验（错误**定位到具体位置或偏移字段**）：

- 名称仅含空白或控制字符：`blank_placement_name`，定位到 `placements.<下标>.name`；
- 名称重复：`duplicate_placement_name`，定位到 `placements.<下标>.name`（重复出现的后者）；
- 名称重复与偏移类型错误同时出现时，两个错误会一并返回；
- 列表为空 / 超过 50 个：`too_short` / `too_long`，定位到 `placements`；
- 平移后任一车辆顶点坐标越过 ±1,000,000：`translated_coordinate_out_of_range`，
  定位到 `placements.<下标>.dx` 或 `placements.<下标>.dy`。

### `POST /api/profiles/compare`

断面变化比对：输入同一断面的**基准测点**与**本期测点**两组数据，先以共同基准点的
坐标差平移全部本期测点（排除仪器整体平移），再逐点计算与同名基准测点的欧氏位移，
形成可保存的断面变化报告。

请求体（坐标为**毫米整数**，绝对值不超过 1,000,000）：

| 字段 | 说明 |
| --- | --- |
| `baseline_points` | 基准测点列表（至少 1 个），每项 `{"name": <str>, "x": <int>, "y": <int>}`，名称组内唯一 |
| `current_points` | 本期测点列表，名称与顺序须与 `baseline_points` **完全一致** |
| `reference_point` | 两组中共同存在的基准点名称，用于对齐整体平移 |
| `tolerance` | 位移容差（毫米，**非负整数**）；未舍入位移 `<=` 容差判为合格 |

```bash
curl -s http://localhost:8000/api/profiles/compare \
  -H 'Content-Type: application/json' \
  -d '{
    "baseline_points": [
      {"name": "L1", "x": 0,   "y": 0},
      {"name": "C1", "x": 500, "y": 1500},
      {"name": "R1", "x": 1000, "y": 0}
    ],
    "current_points": [
      {"name": "L1", "x": -7,  "y": 11},
      {"name": "C1", "x": 496, "y": 1515},
      {"name": "R1", "x": 993, "y": 11}
    ],
    "reference_point": "L1",
    "tolerance": 4
  }'
```

响应（修正量 `(7, -11)`，C1 修正后 `(503, 1504)`、位移 5mm 超限）：

```json
{
  "correction": {"dx": 7, "dy": -11},
  "points": [
    {"name": "L1", "x": 0, "y": 0, "displacement_mm": 0.0},
    {"name": "C1", "x": 503, "y": 1504, "displacement_mm": 5.0},
    {"name": "R1", "x": 1000, "y": 0, "displacement_mm": 0.0}
  ],
  "max_displacement_name": "C1",
  "max_displacement_mm": 5.0,
  "exceeded_names": ["C1"],
  "all_passed": false
}
```

字段说明：

- `correction`：对齐修正量，等于基准组基准点坐标减去本期组基准点坐标；
- `points`：逐点**修正后坐标**与位移，顺序与输入本期测点一致；位移保留三位小数；
- `max_displacement_name` / `max_displacement_mm`：位移最大的测点及其位移；
  **并列时取输入顺序靠前者**；
- `exceeded_names`：位移超过容差的测点名称，按输入顺序排列；
- `all_passed`：全部测点均未超限时为 `true`。

判定语义与净距接口一致：容差比较使用**未舍入**的双精度位移（位移恰好等于容差判合格），
只有输出字段四舍五入到三位小数。因此可能出现位移显示 `2000.0` 但容差 2000mm
仍判超限的情况。

比对特有的 `422` 校验（**任何校验失败都不生成部分报告**，错误定位到具体列表项或字段）：

- 两组名称顺序不一致：`point_name_mismatch`，定位到 `current_points.<下标>.name`（首个分歧处）；
- 两组数量不一致：`point_count_mismatch`，定位到 `current_points`；
- 组内名称重复：`duplicate_point_name`，定位到对应组的 `.<下标>.name`（重复出现的后者）；
- 基准点缺失：`reference_point_missing`，定位到 `reference_point`；
- 容差为负：`greater_than_equal`，定位到 `tolerance`；
- 修正后坐标越过 ±1,000,000：`corrected_coordinate_out_of_range`，
  定位到 `current_points.<下标>.x` 或 `.<下标>.y`；
- 测点名称仅含空白字符：`blank_point_name`；坐标非整数 / 越界、多余字段等同既有规则。

### `POST /api/profiles/clearance-impact`

两期限界影响复核：断面复核人员确认测点变化后，进一步判断这些变化是否让车辆限界
失去安全净距。输入两期测点、共同基准点、车辆限界与要求净距，先按既有基准点规则
修正本期坐标，再把两期测点序列分别作为**不闭合隧道折线**复用净距算法，一次给出
两期结论与净距变化。

请求体（坐标为**毫米整数**，绝对值不超过 1,000,000）：

| 字段 | 说明 |
| --- | --- |
| `baseline_points` | 基准测点列表（**至少 2 个**，按顺序构成不闭合折线），名称组内唯一 |
| `current_points` | 本期测点列表，名称与顺序须与 `baseline_points` **完全一致** |
| `reference_point` | 两组中共同存在的基准点名称，用于对齐整体平移 |
| `vehicle_polygon` | 车辆限界多边形，规则同 `check` |
| `required_clearance` | 要求净距（毫米整数） |

```bash
curl -s http://localhost:8000/api/profiles/clearance-impact \
  -H 'Content-Type: application/json' \
  -d '{
    "baseline_points": [
      {"name": "L1", "x": 0,   "y": 0},
      {"name": "L2", "x": 0,   "y": 1200},
      {"name": "C1", "x": 500, "y": 1500},
      {"name": "R2", "x": 1000,"y": 1200},
      {"name": "R1", "x": 1000,"y": 0}
    ],
    "current_points": [
      {"name": "L1", "x": -7,  "y": 11},
      {"name": "L2", "x": -7,  "y": 1211},
      {"name": "C1", "x": 493, "y": 1111},
      {"name": "R2", "x": 993, "y": 1211},
      {"name": "R1", "x": 993, "y": 11}
    ],
    "reference_point": "L1",
    "vehicle_polygon": {
      "points": [
        {"x": 200, "y": 200},
        {"x": 800, "y": 200},
        {"x": 800, "y": 1000},
        {"x": 200, "y": 1000}
      ]
    },
    "required_clearance": 150
  }'
```

响应（修正量 `(7, -11)`，C1 修正后 `(500, 1100)` 距限界顶边仅 100mm，由合格转为不合格）：

```json
{
  "baseline": {
    "passed": true,
    "minimum_clearance_mm": 200.0,
    "required_clearance_mm": 150,
    "intersects": false,
    "dangerous_pair": { "tunnel_segment": {}, "vehicle_segment": {}, "distance_mm": 200.0 }
  },
  "current": {
    "passed": false,
    "minimum_clearance_mm": 100.0,
    "required_clearance_mm": 150,
    "intersects": false,
    "dangerous_pair": {
      "tunnel_segment": {
        "start_index": 1,
        "start": {"x": 0, "y": 1200},
        "end": {"x": 500, "y": 1100}
      },
      "vehicle_segment": {},
      "distance_mm": 100.0
    }
  },
  "clearance_change_mm": -100.0,
  "became_noncompliant": true
}
```

字段说明：

- `baseline` / `current`：基准期与本期（**修正后**）折线的完整净距结论，
  结构与判定语义同 `check`（未舍入距离比较、三位小数舍入、危险边并列选择）；
  本期危险边端点展示**修正后**坐标，线段起点索引对应测点序列下标；
- `clearance_change_mm`：本期最小净距减基准期最小净距，基于**未舍入**距离求差后
  四舍五入到三位小数；负值表示净距被侵蚀；
- `became_noncompliant`：基准期合格而本期不合格时为 `true`；
  两期均不合格或均合格时均为 `false`。

影响复核特有的 `422` 校验（复用既有错误信封，**任何校验失败都不产生半份影响报告**）：

- 名称顺序 / 数量不一致、组内重名、基准点缺失、修正后坐标越界：与 `compare` 完全相同的
  错误类型与字段定位；
- 任一期相邻测点重合（零长线段无法构成折线）：`duplicate_adjacent_point`，
  定位到 `baseline_points.<下标>` 或 `current_points.<下标>`；
- 任一期测点少于 2 个：`too_short`，定位到对应测点列表；
- 车辆轮廓无效（自交 / 零面积 / 重复首点等）：与 `check` 完全相同的校验规则。

交互式文档：启动后访问 `http://localhost:8000/docs`。

## 几何规则与判定语义

- 线段相交判定：严格跨越（异号叉积）或退化接触（端点落在对方线段上、共线重叠）；
  相交或接触的距离为 **0**。
- 线段间距离：不相交时取四个端点到对方线段距离的最小值；点到线段距离在垂足落于
  线段外时退化为到较近端点的距离。
- 隧道折线按输入顺序连接、**不**追加首尾边；限界多边形**追加**一条首尾隐式闭合边。
- 通过门槛使用**未舍入**的双精度最小距离比较；只有输出字段四舍五入到三位小数。
  因此可能出现响应显示 `7.000` 但要求 7mm 仍判不通过的情况（测试
  `test_gate_uses_unrounded_distance` 用一组丢番图整数坐标精确覆盖该边界）。
- 危险对唯一性（并列规则）：两个距离之差不超过 `1e-9` 视为并列，依次比较
  ① 隧道线段起点索引 ② 限界边起点索引，取较小者；差超过 `1e-9` 则更近者胜。
- 自交检查只针对限界多边形，且跳过相邻边（相邻边共享端点是正常的）；
  非相邻边接触也判自交。
- 退化检查也只针对限界多边形：所有有效顶点共线导致面积为零时拒绝；此时隐式
  闭合边会与其它边重叠，不能进入净距计算。

## 测试

```bash
pytest
```

覆盖内容包括：普通交叉 / T 形 / 端点相接 / 共线重叠的相交判定，垂足在线段内外的
点线距离，自交多边形（蝴蝶结、非相邻边接触）与凹多边形，并列 `1e-9` 阈值的两侧边界，
闭合边成为最危险边，零面积共线轮廓拒绝，大坐标双精度，`ROUND_HALF_UP` 的 `.0005` 进位，
未舍入门槛，全部字段级错误定位，批量位置复核（全过 / 首个失败不中断 /
相交位置 / 空白名称 / 平移越界与重名的字段级错误 / 重名与偏移错误同时返回 /
数量边界 / 与单点接口结论一致），以及断面两期比对（纯整体平移全过 / 单点真实位移定位 /
最大位移并列取输入顺序靠前者 / 未舍入容差门槛 / 名称顺序与数量不一致拒绝 /
组内重名 / 基准点缺失 / 负容差 / 修正后坐标越界 / 测点字段级错误），两期限界影响复核
（纯整体平移两期结论一致 / 局部变形由合格转为不合格 / 相交净距归零 / 未舍入门槛 /
危险边并列稳定 / 相邻测点重合与各类无效输入拒绝 / 不产生半份影响报告），
以及 docker-compose 的 API_PORT 端口映射编排守卫。
