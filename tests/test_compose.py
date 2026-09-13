"""docker-compose 编排守卫：API_PORT 端口映射与 verify 验收编排行为不变。"""

from pathlib import Path

COMPOSE = Path(__file__).resolve().parent.parent / "docker-compose.yml"


def test_api_port_mapping_preserved():
    compose = COMPOSE.read_text(encoding="utf-8")
    # 宿主端口由 API_PORT 环境变量覆盖，默认 8000；容器内固定监听 8000
    assert '"${API_PORT:-8000}:8000"' in compose
    # verify 一次性验收服务复用 api 镜像并对容器内 8000 端口执行断言
    assert "BASE_URL: http://api:8000" in compose
    assert "pull_policy: never" in compose
