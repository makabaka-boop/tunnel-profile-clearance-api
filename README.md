# 隧道限界复核 API

纯后端 JSON API：输入**激光测量导出的隧道断面折线**与**车辆限界多边形**（坐标均为毫米整数），
计算两组线段之间的全局最小欧氏距离，判定车辆限界是否满足指定净距，并定位**唯一的最危险线段对**。
支持单点复核与**批量平移位置复核**（同一断面上一至五十个摆放位置一次核验）。
全程不依赖 CAD 软件，也不依赖任何第三方几何库——线段相交、点到线段距离均为自行实现。

- Python 3.12 · FastAPI · Pydantic v2
- 内部计算使用双精度浮点（IEEE-754 double）
- 响应距离统一**四舍五入到小数点后三位**（`ROUND_HALF_UP`）

## 目录结构

```
app/
  geometry.py     # 线段相交 / 点到线段距离 / 两组线段全局最小距离（纯 Python）
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
  批量位置全过 / 首个失败不中断 / 平移越界定位 / 旧接口响应不变），
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
pytest                 # 66 项测试
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
| `placements[].name` | 位置名称，非空字符串，**同一批次内唯一** |
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

- 名称重复：`duplicate_placement_name`，定位到 `placements.<下标>.name`（重复出现的后者）；
- 列表为空 / 超过 50 个：`too_short` / `too_long`，定位到 `placements`；
- 平移后任一车辆顶点坐标越过 ±1,000,000：`translated_coordinate_out_of_range`，
  定位到 `placements.<下标>.dx` 或 `placements.<下标>.dy`。

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

## 测试

```bash
pytest
```

覆盖内容包括：普通交叉 / T 形 / 端点相接 / 共线重叠的相交判定，垂足在线段内外的
点线距离，自交多边形（蝴蝶结、非相邻边接触）与凹多边形，并列 `1e-9` 阈值的两侧边界，
闭合边成为最危险边，大坐标双精度，`ROUND_HALF_UP` 的 `.0005` 进位，
未舍入门槛，全部字段级错误定位，以及批量位置复核（全过 / 首个失败不中断 /
相交位置 / 平移越界与重名的字段级错误 / 数量边界 / 与单点接口结论一致）。
