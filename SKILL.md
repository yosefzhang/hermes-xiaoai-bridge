---
name: hermes-xiaoai-bridge
description: 通过小爱音箱作为语音通道与 Home Assistant 交互。当用户说"让小爱说…"、"小爱播报…"、"告诉小爱…"、"用小爱控制…"、"监听小爱"、"开启语音监听"时，必须使用此技能——即使你已经能直接操作 HA，只要涉及小爱音箱这个通道，就应该加载此技能。
---

# hermes-xiaoai-bridge

实现两个场景：
- `让小爱音箱做某事`：用户通过消息渠道等方式对Hermes发送`要求小爱做某事`的指令时，Hermes 解析意图后转发给小爱音箱执行。
- `小爱音箱不能做某事时，转交给 Hermes 处理`：用户通过小爱同学唤醒小爱音箱，执行指令时小爱音箱无法理解或执行，小爱音箱把对话内容发给 Hermes，Hermes 处理后通过小爱播报结果。

## 让小爱音箱做某事
消息传递路径：用户 → Hermes → 小爱音箱 → Hermes → 用户
典型场景：
- 用户通过微信/飞书渠道发送"让小爱打开客厅灯"

### 执行步骤

#### Step 1 — Hermes 解析指令
Hermes 解析用户消息，识别出是希望小爱做某事，并提取出具体的指令内容（如"打开客厅灯"）

#### Step 2 — Hermes 转发指令给小爱

`<content>` 是用户的指令文本（如"打开客厅灯"），`<speaker_name>` 是指定小爱音箱的名称（如"卧室小爱"）

```bash
mijiaAPI run <content> --wifispeaker_name <speaker_name> -p ~/.config/hermes-xiaoai-bridge/auth.json

# 示例
mijiaAPI run "把亮度调到50%" --wifispeaker_name "卧室小爱" -p ~/.config/hermes-xiaoai-bridge/auth.json
```

#### Step 3 — 小爱执行指令并回复结果
小爱音箱接收到指令后执行，Hermes 延迟 3 秒后拉取对话记录，通过对话内容判断小爱是否成功执行了指令，并将结果反馈给用户。

```bash
mijiaAPI conversations --speaker_name <speaker_name> --limit 2 -p ~/.config/hermes-xiaoai-bridge/auth.json

# 示例
mijiaAPI conversations --speaker_name "卧室小爱" --limit 2 -p ~/.config/hermes-xiaoai-bridge/auth.json
```

## 小爱音箱不能做某事时，转交给 Hermes 处理
消息传递路径：用户 → 小爱音箱 → Hermes → 小爱音箱
典型场景：
- 用户对小爱说"把主卧空调调成制冷"，小爱无法理解 → 小爱把对话内容发给 Hermes → Hermes 解析意图后通过 HA 执行 → Hermes 通过小爱播报"已帮你把主卧空调调成制冷了"

### 前置条件
[ ] 完成webhook路由配置，确保小爱音箱的对话事件能正确触发 Hermes 的 agent session 
[ ] monitor.py 正常运行，能够检测到小爱对话事件并 POST 到 webhook
[ ] config.yaml 已配置 platform_toolsets（webhook: homeassistant, terminal, file, skills），否则 agent session 无法操作 HA 也无法调用 mijiaAPI play-text → 音箱沉默

### 执行步骤

#### Step 1 — webhook 接收小爱对话事件
当用户对小爱说话时，monitor.py 轮询检测到新的对话事件后，POST JSON 数据到 webhook。

#### Step 2 — Hermes 判断小爱是否已成功执行指令
- 小爱已成功执行指令 --> Hermes 不做处理，等待用户下一次交互
- 小爱未成功执行指令 --> Hermes 处理指令并通过小爱播报结果

```bash
mijiaAPI play-text <text> --wifispeaker_name <speaker_name> -p ~/.config/hermes-xiaoai-bridge/auth.json
# 示例
mijiaAPI play-text "晚安" --wifispeaker_name "卧室小爱" -p ~/.config/hermes-xiaoai-bridge/auth.json
```

### 典型场景
小爱音箱无法直接操作 Home Assistant 中的设备，需要转交给 Hermes 处理：
- Step 1：用户说："打开主卧空调"
- Step 2：小爱音箱无法理解指令，回复"没有发现相关设备"
- Step 3：monitor.py 检测到小爱对话事件，POST 到 webhook
- Step 4：Hermes 解析用户意图，识别出用户想打开主卧空调
- Step 5：Hermes 在 Home Assistant 中找到主卧空调设备，执行打开操作
- Step 6：Hermes 通过小爱音箱播报“已帮你打开主卧空调了，当前主卧空调为制冷模式，温度设置为24度”



## 首次配置

### Step 1 - 生成配置文件

默认创建 `~/.config/hermes-xiaoai-bridge/config.json`，写入：

```json
{
    "workspace": "~/.config/hermes-xiaoai-bridge",  # 工作目录，存储 auth.json，log 等
    "log_level": "INFO",                            # 日志级别
    "default_wifispeaker": "",                      # 默认使用的小爱音箱名称
    "monitor": {                                    # monitor.py 配置
        "enabled": true,
        "wifispeakers": [],
        "poll_interval": 1,                         # 轮询间隔，单位秒
        "webhook": ""                               # Hermes webhook 地址
    }
}
```

### Step 2 - 安装依赖

```bash
cd ~/.hermes/skills/hermes-xiaoai-bridge
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Step 3 - 登录

#### Step 3.1 - 生成 QR 链接

```bash
mijiaAPI login -g -p ~/.config/hermes-xiaoai-bridge/auth.json
```

执行后会在同目录生成状态文件 `auth.json.login_state.json`，其中包含 `qr_image_url` 链接。

#### Step 3.2 - 发送链接 → 立即启动轮询

1. 从状态文件中读取 QR 链接：

```bash
cat ~/.config/hermes-xiaoai-bridge/auth.json.login_state.json
```

2. 将 `qr_image_url` 链接发送给用户（不要下载图片）。

3. **立即**在后台启动轮询（不要等用户确认）：

```bash
mijiaAPI login --poll -p ~/.config/hermes-xiaoai-bridge/auth.json
```

#### Step 3.3 - 用户扫码 → 自动保存 auth.json

用户用手机浏览器打开链接，米家 App 扫码授权后，`--poll` 命令会自动检测到并保存 `auth.json`。

#### Step 3.4 - 验证登录

```bash
mijiaAPI -l -p ~/.config/hermes-xiaoai-bridge/auth.json
```

如果能正常返回设备列表，说明登录成功。


### Step 4 - 让用户指定音箱

1. 列出账号下所有设备，展示给用户（含 wifispeaker 音箱）：

```bash
mijiaAPI -l -p ~/.config/hermes-xiaoai-bridge/auth.json
```

> **识别小爱音箱的方法**：
> - `hardware` 字段为已知型号（如 `L16A`、`L06A` 等）→ 是小爱音箱
> - `model` 字段包含 `wifispeaker` → 是小爱音箱（旧方法，可能失效）

2. 让用户从设备列表中选择一个作为默认音箱（`default_wifispeaker`）。

3. 让用户选择需要 monitor.py 监听哪些音箱（`monitor.wifispeakers`），可多选或全选。

### Step 5 - 询问用户是否启动 monitor.py 监听小爱对话事件

1. 询问用户是否启用监听（`monitor.enabled`）。如果启用，填写监听音箱列表（即 Step 4 第 3 步收集的音箱名称列表）和轮询间隔（`monitor.poll_interval`，默认 1 秒）。

2. 如果启用监听，提醒用户还需完成下方的 Webhook 路由配置，Hermes 才能接收对话事件。

### Step 6 - 获取 webhook 地址

monitor.py 检测到新对话后需要 POST 到 Hermes webhook。从 Hermes 配置中找到 webhook 地址，填入 `config.json` 的 `webhook` 字段。

1. 检查 `~/.hermes/config.yaml` 中 `platforms.webhook.extra` 的 `host` 值（默认 `127.0.0.1`）。
2. 检查 `~/.hermes/.env` 中 `WEBHOOK_PORT` 的值（默认 `9995`）。
3. 路由名为 `hermes-xiaoai-bridge`，地址格式：

   ```
   http://{host}:{port}/webhooks/hermes-xiaoai-bridge
   ```

   示例：`http://127.0.0.1:9995/webhooks/hermes-xiaoai-bridge`

4. 将地址写入 `config.json` 的 `monitor.webhook` 字段。

### Step 7 - 更新配置

根据上述收集的信息更新 `~/.config/hermes-xiaoai-bridge/config.json`

### Step 8 - 启动 monitor.py

1. 启动 monitor.py 监听小爱对话事件：

```bash
cd ~/.hermes/skills/hermes-xiaoai-bridge
source .venv/bin/activate
python script/monitor.py
```

2. 检查monitor.py日志，确认已成功连接小爱音箱并开始监听对话事件。

## Webhook 路由增加 hermes-xiaoai-bridge

### Step 1 - 启用webhook

检查是否启用了webhook，如果没有启用，按照hermes官方文档启用webhook功能。

### Step 1 - 检查webhook是否具备包含以下toolsets
```yaml
platform_toolsets:
  webhook:
    - homeassistant
    - terminal
    - file
    - skills
```

### Step 3 - 新增 routes: hermes-xiaoai-bridge
- skills: ["hermes-xiaoai-bridge"]

- prompt:
```
小爱音箱新对话事件。

Variables:
   {speaker} — 触发新对话事件的音箱
   {query}   — 用户的输入（如"主卧空调调成制冷"）
   {answer}  — 小爱 AI 回复（可能包含失败关键词或为空）

3-step 推理：只有小爱无法处理时，才由你通过 HA 接管。

Step 1 — 小爱是否已自行处理成功？
检查 {answer}：
- answer 包含"没有发现"/"学习中"/"先去米家绑定"等失败关键词 → 小爱失败了，继续 Step 2
- answer 非空且不包含失败关键词 → 小爱已自行处理，STOP
- answer 为空或无 answer → 继续 Step 2（可能小爱未识别）

Step 2 — HA 里有没有这个设备/场景？
解析 {query} 中的设备名和动作意图（如"主卧空调"+"调成制冷"）。
用 ha_list_entities 搜索匹配的实体（支持名称模糊匹配）。
注意：可能匹配到多个相似实体（如 sensor.xxx_temp 和 climate.xxx），优先使用 climate/switch/light 等控制类实体。
- 找到匹配的实体 → 继续 Step 3
- 没找到 → 回复"没找到相关设备"并通过 TTS 播报，STOP

Step 3 — 执行 HA 操作并通过小爱播报结果
根据解析的意图执行相应的 HA 操作（如 climate.set_mode, climate.set_temperature, switch.turn_on, light.turn_on 等）。

使用 mijiaAPI play-text 播报结果：

mijiaAPI play-text "设备已操作" --wifispeaker_name "{speaker}" -p ~/.config/hermes-xiaoai-bridge/auth.json
```

### 步骤四：重启 Gateway

```bash
hermes gateway restart
```