# 完整的输入流程

> 作者：小新  
> 日期：2026-06-05  
> 来源：IMA 笔记整理

---

## 整体概览

你在前端输入："统计华北地区的销售总额"

```
POST /api/query  请求体: {"query": "统计华北地区的销售总额"}
     ↓
query_router.py 收到请求
     ↓
query_service.py 中：
    state = DataAgentState(query="统计华北地区的销售总额")     ← 在这里初始化
     ↓
graph.astream(input=state, ...)     ← 丢进 LangGraph 图开始跑
     ↓
extract_keywords(state) 节点：state["query"] → jieba 分词 → 提取关键词
```

---

## 第 1 步：前端发请求

```
POST /api/query
Content-Type: application/json

{"query": "统计华北地区的销售总额"}
```

---

## 第 2 步：FastAPI 路由接收

`app/api/routers/query_router.py`

```python
@query_router.post("/api/query")
async def query_handler(
    query: QuerySchema,        # FastAPI 自动解析 JSON → QuerySchema
    query_service: Annotated[QueryService, Depends(get_query_service)],
):
    return StreamingResponse(
        query_service.query(query.query),
        media_type="text/event-stream",
    )
```

- `query` 是 FastAPI 从请求体自动解析的 `QuerySchema` 对象
- `query.query` 是里面的字符串字段："统计华北地区的销售总额"
- `query_service.query(...)` 是 `QueryService` 的方法，启动工作流

---

## 第 3 步：QueryService 初始化 State

`app/services/query_service.py`

```python
state = DataAgentState(query="统计华北地区的销售总额")
```

此时其余字段（`keywords`、`retrieved_column_infos` 等）都是空的，还没填。

---

## 第 4 步：LangGraph 开始跑

这一步是整个系统的发动机。拆成三块来说：

### 4.1 `graph.astream()` 是什么

```python
# 简写
async for chunk in graph.astream(input=state, context=context, stream_mode="custom"):
    yield f"data: {json.dumps(chunk)}\n\n"
```

**等价展开理解：**

```python
# LangGraph 内部大致是这样执行的
current_state = {"query": "统计华北地区的销售总额"}

for node_func in [extract_keywords, recall_column, recall_value,
                   recall_metric, merge_retrieved_info, ...]:
    # 每个节点的签名：async def node(state, runtime)
    #   - state: 读当前状态
    #   - runtime.context: 读外部依赖（Qdrant、MySQL 等）
    #   - runtime.stream_writer: 写进度消息
    node_updates = await node_func(current_state, runtime)

    # 节点返回的 dict 自动合并到 state
    current_state.update(node_updates)
```

### 4.2 `stream_mode="custom"` 特殊在哪

| 模式 | 每次 yield 什么 |
|------|----------------|
| 默认 | 每个节点执行完后，yield 完整的新 state |
| "updates" | 每个节点执行完后，yield 该节点返回的增量 dict |
| **"custom"**（项目用） | 只 yield 节点里主动 `writer()` 的内容 |

每个节点内部：

```python
async def extract_keywords(state, runtime):
    writer = runtime.stream_writer

    writer({"type": "progress", "step": "抽取关键词", "status": "running"})
    # ↑ 这一步立即让 astream 迭代器 yield 出这个 chunk

    keywords = jieba.analyse.extract_tags(state["query"])

    writer({"type": "progress", "step": "抽取关键词", "status": "success"})
    # ↑ 又 yield 一个 chunk

    return {"keywords": keywords}  # 更新 state
```

### 4.3 前端收到的 SSE 流

```
data: {"type":"progress","step":"抽取关键词","status":"running"}
data: {"type":"progress","step":"抽取关键词","status":"success"}
data: {"type":"progress","step":"召回字段信息","status":"running"}
data: {"type":"progress","step":"召回字段信息","status":"success"}
...(中间节点依次输出 running/success)...
data: {"type":"progress","step":"执行SQL","status":"success"}
data: {"type":"result","data":[["地区","销售额"],["华北",125000]]}
```

---

## 一句话总结

`graph.astream(input=state, context=context, stream_mode="custom")`：

- `input` → 初始问题放进去
- `context` → 把 Qdrant、MySQL 这些"工具"塞给所有节点用
- `stream_mode="custom"` → 只输出节点主动报告的进度消息
- 每个 `writer(...)` 调用 → yield 一个 chunk → 包装成 SSE → 推给前端显示进度条
