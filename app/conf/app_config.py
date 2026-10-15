"""
应用主配置

定义 conf/app_config.yaml 在程序中的结构化配置对象
项目启动后会在这里一次性完成配置文件加载和类型化转换，其他模块只需要导入 app_config
就可以按属性方式读取日志 MySQL Qdrant Embedding Elasticsearch 和 LLM 配置
"""

from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from omegaconf import OmegaConf


@dataclass
class File:
    """文件日志配置"""

    enable: bool
    level: str
    path: str
    rotation: str
    retention: str


@dataclass
class Console:
    """控制台日志配置"""

    enable: bool
    level: str


@dataclass
class LoggingConfig:
    """日志总配置"""

    file: File
    console: Console


@dataclass
class DBConfig:
    """MySQL 连接配置"""

    host: str
    port: int
    user: str
    password: str
    database: str


@dataclass
class QdrantConfig:
    """Qdrant 连接与向量维度配置"""

    host: str
    port: int
    embedding_size: int


@dataclass
class EmbeddingConfig:
    """Embedding 服务配置"""

    host: str
    port: int
    model: str


@dataclass
class ESConfig:
    """Elasticsearch 配置"""

    host: str
    port: int
    index_name: str


@dataclass
class LLMConfig:
    """大模型调用配置"""

    model_name: str
    api_key: str
    base_url: str
    # 关闭思维链（thinking）的节点名列表。这些节点属“轻任务”（关键词扩展、表/指标过滤），
    # 关闭后单次调用耗时与 token 大幅下降；缺省为空表示所有节点都保留 thinking。
    no_think_nodes: list[str] = field(default_factory=list)


@dataclass
class SQLGuardConfig:
    """SQL 安全沙箱配置"""

    max_rows: int


@dataclass
class ObservabilityConfig:
    """链路追踪（可观测）配置"""

    # 总开关：关闭时节点不做任何埋点，追踪零开销
    enable: bool
    # 采样比例（0~1）：1.0 表示全量采集，小于 1 时按比例抽样
    sample_rate: float
    # 是否落 trace_span 明细：关闭时只写 trace 头表（高并发场景省写入）
    store_span: bool


@dataclass
class RecallConfig:
    """多路召回的并发控制配置"""

    # 单路召回内部并发检索的最大并发数；<=0 表示不限制（等价于全量并发）
    concurrency: int = 8


@dataclass
class RoleConfig:
    """单个角色的权限配置"""

    # 允许访问的区域 id 白名单；为空表示不做行级过滤（全量可见）
    region_ids: list[str]
    # 需要列级脱敏的字段名列表（按结果列名匹配）
    mask_columns: list[str]


@dataclass
class RBACConfig:
    """角色权限总配置"""

    default_role: str
    roles: dict[str, RoleConfig]

    def resolve_name(self, role: str | None) -> str:
        """返回生效的角色名；未知或缺失一律回落到 default_role（最小权限原则）。"""
        return role if role and role in self.roles else self.default_role

    def resolve(self, role: str | None) -> RoleConfig:
        """把请求携带的角色解析成权限配置。

        未知或缺失的角色一律回落到 default_role，避免"给不出角色就放行全量"。
        """
        return self.roles.get(self.resolve_name(role)) or RoleConfig(
            region_ids=[], mask_columns=[]
        )


@dataclass
class AuthConfig:
    """鉴权（JWT）配置

    核心不变量：**角色由服务端决定，客户端无法指定**。
    客户端只能携带服务端签发过的令牌，改内容就会导致签名不匹配。

    刻意**不提供"关闭鉴权"的开关**：配置里的一个 ``false`` 就能把系统敞开，
    是比漏洞本身更常见的事故来源。确需本地绕过时，请改代码并走 review，
    让"关掉鉴权"这件事在 diff 里看得见。
    """

    # 签名密钥，来自环境变量 JWT_SECRET；为空或过短时拒绝启动
    secret_key: str
    # 服务端固定校验的算法。绝不采用令牌头里的 alg，避免算法混淆绕过验签
    algorithm: str
    # 签发者标识；校验时比对，避免别的系统签的令牌被拿来用
    issuer: str
    # 令牌有效期（分钟）
    expire_minutes: int
    # 演示用签发点开关；生产环境必须为 False
    dev_login_enable: bool = False
    # 演示用共享口令（来自环境变量 DEV_LOGIN_PASSWORD）
    dev_login_password: str = ""
    # 演示用账号 → 角色映射。角色按用户名查表得出，客户端只能选账号、不能选角色
    dev_users: dict[str, str] = field(default_factory=dict)

    def resolve_role_of(self, username: str) -> str | None:
        """按用户名查出角色；查不到返回 None（调用方应拒绝签发令牌）。"""
        return self.dev_users.get(username)


@dataclass
class AppConfig:
    """项目级总配置入口"""

    logging: LoggingConfig
    db_meta: DBConfig
    db_dw: DBConfig
    qdrant: QdrantConfig
    embedding: EmbeddingConfig
    es: ESConfig
    llm: LLMConfig
    sql_guard: SQLGuardConfig
    rbac: RBACConfig
    auth: AuthConfig
    observability: ObservabilityConfig
    query_run_timeout_seconds: int = 300
    # 召回并发配置带默认值：即使配置文件里没有 recall 段也能正常启动
    recall: RecallConfig = field(default_factory=RecallConfig)


# 从当前文件位置回到项目根目录，再定位到 conf/app_config.yaml
project_root = Path(__file__).parents[2]
config_file = project_root / "conf" / "app_config.yaml"

# 先读取本地 .env，让 YAML 中的 ${oc.env:...} 可以解析到敏感配置
load_dotenv(project_root / ".env")

# 读取 YAML 配置内容
context = OmegaConf.load(config_file)

# 根据 AppConfig 生成结构化配置 schema
schema = OmegaConf.structured(AppConfig)

# 把配置结构和配置值合并，再转换成可以直接按属性访问的对象
app_config: AppConfig = OmegaConf.to_object(OmegaConf.merge(schema, context))

if __name__ == "__main__":
    # 简单测试：验证配置是否能正常读取
    print(app_config.es.host)
