# hermes-xiaoai-bridge

> Hermes Agent ↔ 小爱音箱 双向语音桥梁。把小爱音箱变成 Home Assistant 语音入口。

底层依赖 [mijiaAPI](https://github.com/yosefzhang/mijia-api)（小米 MIoT / MINA API 封装库）。

## 场景一：Hermes → 小爱（转发模式）

用户通过微信/飞书等渠道让 Hermes 操控小爱音箱。

```mermaid
sequenceDiagram
    actor 用户
    participant Hermes
    participant 小爱音箱

    用户->>Hermes: "让小爱打开客厅灯"
    Hermes->>小爱音箱: mijiaAPI run "打开客厅灯"
    note right of 小爱音箱: 音箱播报并执行
    Hermes-->>Hermes: 等待 3 秒
    Hermes->>小爱音箱: mijiaAPI conversations 拉取回复
    小爱音箱-->>Hermes: {query, answer}
    Hermes-->>用户: "已帮你打开客厅灯了"
```

## 场景二：小爱 → Hermes（监听模式）

用户直接对小爱说话，小爱无法处理时转交 Hermes 接管。

```mermaid
sequenceDiagram
    actor 用户
    participant 小爱音箱
    participant monitor.py
    participant Hermes
    participant HomeAssistant

    用户->>小爱音箱: "把主卧空调调成制冷"
    小爱音箱-->>用户: "没有发现相关设备"
    loop 轮询
        monitor.py->>小爱音箱: 拉取对话记录
    end
    小爱音箱-->>monitor.py: {query, answer}
    monitor.py->>Hermes: POST webhook
    Hermes->>Hermes: 解析意图
    Hermes->>HomeAssistant: 执行操作（调空调）
    HomeAssistant-->>Hermes: 操作完成
    Hermes->>小爱音箱: TTS 播报结果
    小爱音箱-->>用户: "已帮你把主卧空调调成制冷了"
```
