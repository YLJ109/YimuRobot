# 埃夫特 ER3-600 六轴工业机器人 3D 仿真系统

支持 **AI 大模型自然语言控制（语音 + 文字）** 的埃夫特六轴工业机器人 3D 仿真教学演示系统。前端浏览器实时渲染机器人运动学与场景，后端用 Flask + SocketIO 驱动虚拟控制器，配套智谱 GLM 大模型做指令理解与程序生成、faster-whisper 做本地语音识别、edge-tts 做语音播报。

> 定位：华为 Demo 场景。零硬件依赖，纯仿真；适合答辩、演示、教学。

---

## 功能特性

- **3D 可视化**：埃夫特 ER3-600 六轴机械臂，Three.js 渲染，旋转 / 缩放 / 平移视角，舞台底色与外壳分离不刺眼。
- **场景编辑**：拖拽摆放物体（方块 / 圆柱 / 球 / 托盘），增删改，坐标与颜色即时落到 3D 数模，可保存 / 加载。
- **手动控制**：J1–J6 滑块实时控制关节角度（±170°），笛卡尔位姿滑杆（逆解下发），实时显示 TCP 坐标。
- **编程控制**：自定义 DSL 机器人语言，CodeMirror 6 语法高亮，运行 / 停止 / 暂停 / 单步。
- **吸盘工具**：`SUCK ON/OFF` 吸取 / 释放物体（重父化保留世界坐标，视觉接触面与运动学接触面对齐）。
- **几何碰撞检测**：几何近似（不引物理引擎），连杆 / 所持物体 vs 场景物体 + 地面双闸门校验，落点被占时自动避让到最近空位。
- **AI 自然语言控制**：文字 / 语音输入 → 大模型生成 DSL → 自动执行；含糊指令会反问澄清；快通道指令（回家 / 停止）即时响应。
- **每用户 API Key**：页面右上角「设置」填入你自己的智谱 Key，按会话（sid）隔离、互不影响；Key 只存于本浏览器与当前会话内存，不写后端磁盘、不与他人共享。也可共用后端 `.env` 的全局 Key。
- **语音交互**：实时语音识别（faster-whisper，本地 CPU int8）+ 语音播报（edge-tts）。
- **两种语音录入方式**：① 点麦克风按钮；② **长按空格键说话、松开发送**（push-to-talk）。
- **安全围栏**：四面玻璃墙 + 四根玻璃角柱 + 顶盖组成的封闭保护体积，临近度分级着色（绿 → 黄 → 红 → 碰撞红闪），碰撞边沿触发一次语音报警并自动停机；支持缩放 / 调高 / 显隐，位于独立「安全围栏」标签页。
- **安全机制**：DSL 语法校验 + 工作空间边界检查 + 执行期硬上限兜底（防 OOM / 无限等待）。

---

## 技术栈

| 层 | 技术 |
|---|---|
| 前端 | Vue 3 + Vite + Pinia + Element Plus + Three.js + CodeMirror 6 |
| 后端 | Python Flask + Flask-SocketIO（threading 并发模型，运行于 `backend/venv` 虚拟环境） |
| 运动学 | 零依赖自研 6-DOF 逆解（D-H + 阻尼最小二乘 + 多起点兜底 + 关节限位裁剪） |
| 碰撞 | `collision.py` 几何近似（胶囊 vs AABB，不引物理引擎） |
| LLM | 智谱 GLM-4-Plus（Function Calling） |
| ASR | faster-whisper（本地部署，small 模型，CPU int8） |
| TTS | edge-tts（微软云合成） |
| 数据库 | SQLite（`backend/robot_sim.db`，存场景 / 程序，已 gitignore） |

---

## 目录结构

```
HuaweiDemoAgent/
├── backend/                  # 后端（运行在 venv 内）
│   ├── app.py               # Flask 主应用 + SocketIO 事件 + 健康检查 / 音频路由
│   ├── parser.py            # DSL 解析器（唯一从始至终健康的模块）
│   ├── executor.py          # 虚拟控制器（轨迹生成 + 60Hz 帧推送 + 碰撞复查）
│   ├── kinematics.py        # 运动学（D-H 参数 + FK/IK，零依赖自研）
│   ├── collision.py         # 几何碰撞检测（校验期 + 执行期双闸门）
│   ├── validator.py         # 校验器（语法 + 边界 + 限位 + 碰撞预检）
│   ├── ai_gateway.py        # AI 网关（GLM-4 + 快慢通道 + 工具回环）
│   ├── speech.py            # 语音模块（ASR + TTS，HF 镜像下载）
│   ├── requirements.txt      # Python 依赖（含 ASR 栈）
│   ├── .env / .env.example   # 运行配置（API Key 等，已 gitignore）
│   ├── tests/               # 单元测试（pytest，35 passed）
│   └── venv/                # 虚拟环境（自动创建，已 gitignore）
├── frontend/                 # 前端
│   ├── src/
│   │   ├── classes/         # 核心类
│   │   │   ├── RobotArm.js       # 机器人 Three.js 控制
│   │   │   ├── RobotIK.js        # 前端逆解（与后端约定一致）
│   │   │   ├── SceneManager.js   # 场景物体管理（世界坐标上报）
│   │   │   ├── SimSocket.js      # WebSocket 通信封装（含 audio_utterance）
│   │   │   └── robotContext.js   # 机器人上下文
│   │   ├── composables/     # 组合式函数
│   │   │   └── useSpeech.js      # 语音采集（单例 + 长按空格 push-to-talk）
│   │   ├── stores/          # Pinia 状态管理（robot / scene / ai / editor / fence）
│   │   ├── components/      # Vue 组件
│   │   │   ├── RobotViewport.vue # 3D 视口
│   │   │   ├── ManualPanel.vue   # 手动控制面板
│   │   │   ├── SafetyFencePanel.vue # 安全围栏标签页（缩放 / 调高 / 显隐 + 状态）
│   │   │   ├── ObjectLibrary.vue # 物体库
│   │   │   ├── AiChatPanel.vue   # AI 对话面板（示例按钮直接执行）
│   │   │   └── CodeEditor.vue    # 代码编辑器
│   │   ├── App.vue          # 主布局（顶栏 / 三栏 / 状态栏）
│   │   └── main.js          # 入口
│   ├── public/              # 静态资源（favicon.svg、机器人模型 er3_600.glb）
│   ├── package.json
│   └── vite.config.js
├── tools/                    # 验证 / 资产工具
│   ├── console_probe.mjs    # 前端运行时异常 / 失败请求探针
│   ├── ui_probe.mjs         # 前端端到端断言（30 项）
│   ├── ai_chain_probe.mjs   # AI 全链路探针（19 项，真实智谱 API）
│   ├── asr_probe.py         # ASR 闭环验证（TTS 合成 → whisper 识别）
│   ├── ptt_probe.mjs        # 长按空格 push-to-talk 验证（6 项）
│   ├── fence_test.mjs       # 安全围栏分级单测（9 项，纯 Node）
│   ├── fence_probe.mjs      # 安全围栏端到端验证（14 项，含截图）
│   └── step2glb/            # STEP→GLB 模型转换管线（一次性，node_modules 可重装）
├── docs/                     # 文档（代码审查报告等）
├── start.bat / start.sh      # 一键启动（自动建 venv + 装依赖）
├── .gitignore
└── README.md
```

---

## 快速开始

> 推荐直接用一键脚本：它会自动创建 `backend/venv` 虚拟环境、安装依赖、启动前后端。

### Windows

```bat
start.bat          :: 开发模式：vite :3000 + 后端 :5000
start.bat build    :: 构建前端，全部由 :5000 提供
```

### Linux / macOS

```bash
./start.sh           :: 开发模式
./start.sh build     :: 构建前端并由 :5000 提供
./start.sh backend    :: 只启动后端
```

启动后访问：
- 前端：<http://localhost:3000>
- 后端：<http://localhost:5000>
- 健康检查：<http://localhost:5000/api/health>

### 手动启动（等价步骤）

```bash
# 1) 后端虚拟环境
cd backend
python -m venv venv
venv\Scripts\pip install -r requirements.txt   # Windows
# 或 venv/bin/pip install -r requirements.txt    # Linux/macOS

# 2) 配置环境变量（复制后填入智谱 Key）
cp .env.example .env

# 3) 启动后端
venv\Scripts\python app.py

# 4) 另开终端启动前端
cd ../frontend
npm install
npm run dev
```

---

## 环境变量（backend/.env）

| 变量 | 说明 | 默认 |
|---|---|---|
| `ZHIPU_API_KEY` | 智谱 GLM-4 API Key（**可选**：配了即作为全局演示 Key；用户也可在页面「设置」里填自己的 Key） | 无（缺省则各用户在页面配置） |
| `LLM_MODEL` | 大模型名称 | `glm-4-plus` |
| `HF_ENDPOINT` | HuggingFace 镜像源（企业代理挡官方源时改用） | `https://hf-mirror.com` |
| `ASR_HF_ENDPOINT` | 覆盖上面的下载源 | 同 `HF_ENDPOINT` |
| `WHISPER_MODEL` | whisper 模型 | `small` |
| `COLLISION_CHECK` | 碰撞检测开关（排查误报时设 0） | `1` |
| `TTS_AUDIO_KEEP` | 保留最近 N 个 TTS 音频文件 | `100` |

> 语音识别模型首次运行经 `HF_ENDPOINT` 下载（small 约 500MB），之后缓存，无需重复下载。

---

## 语音控制

两种方式录入语音，走同一条链路（MediaRecorder 整段 → `audio_utterance` → faster-whisper → `asr_final`）：

1. **点麦克风按钮**：按住录制、松开发送（按钮显示「识别中…」）。
2. **长按空格键**：按住说话、松开自动发送。

守卫：焦点在输入框时不抢空格（聊天框正常输入）；`Ctrl/Alt/Meta + 空格` 不触发（保住输入法切换）。

### 语音播报开关

顶栏左侧「语音播报」开关（喇叭图标 + `el-switch`）控制 AI 回复是否朗读：

- **开启（默认）**：AI 回复生成后自动用 edge-tts 朗读。
- **关闭**：静默跳过，不播放（后端仍会生成 `audioUrl`，但前端 `speak()` 直接拦截，不做播放）。
- 开关状态用 `localStorage('huawei_tts_enabled')` 持久化，刷新后保持；关闭时若正在播放会立即停止。
- 这是**前端本地偏好**，不发给后端做全局开关 —— 避免一个用户关闭影响其他连着的客户端（会话状态按 sid 隔离）。

---

## 安全围栏

左侧「手动控制」面板的第三个标签页「安全围栏」提供可视化保护体积：

- **几何**：四面玻璃墙 + 四根玻璃角柱 + 顶盖，构成封闭保护体积（机器人任一连杆越界即判定碰撞）。
- **控制**：缩放（范围 300–1400mm，步长 50）、调高（200–1600mm）、显隐开关。默认值 `half=850 / height=1100`，已按机器人真实工作包络留 ≥100mm 余量。
- **分级着色**（临近度 = `(1 − 余量/REF) × 100`，`REF=150mm`）：
  - 余量 ≤ 0 → **碰撞**（红色闪烁）
  - 临近度 ≥ 90%（余量 < 15mm）→ **危险**（红）
  - 临近度 ≥ 80%（余量 < 30mm）→ **警告**（黄）
  - 其他 → **安全**（绿）
- **报警**：进入碰撞的边沿触发一次语音报警（复用 TTS，受语音播报开关约束）并自动停机（`sim stop`）；不每帧触发，避免重复播报。
- 纯前端可视化 + 报警，不写入后端全局状态（遵循会话状态按 sid 隔离原则）。

---

## DSL 语言

```robot
# 注释行
HOME                                    # 回初始位姿
MOVEJ J1=30,J2=-60,J3=60,J4=0,J5=0,J6=0 # 关节运动
MOVELP X=200,Y=0,Z=300,A=180,B=0,C=0    # 笛卡尔直线运动
PICK target=[-150,0,25]                  # 智能抓取
PLACE target=[150,0,25]                  # 智能放置
SUCK ON                                  # 开启吸盘
SUCK OFF                                 # 关闭吸盘
WAIT 2                                   # 延时 2 秒
SPEED 50                                 # 速度 50%
LOOP 3 ... END                           # 循环 3 次
```

---

## AI 控制示例

- 文字：「把红色方块放到 B 区」 → 自动生成 PICK + PLACE 程序并执行
- 语音：「停！」 → 快通道立即停止
- 文字：「抓取那个东西」 → AI 反问澄清（候选物体列表）
- 语音：「回家」 → 快通道执行 HOME

---

## API 与 Socket 事件

### HTTP

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查（含 `asr` 状态块） |
| GET/POST | `/api/scenes` | 场景保存 / 加载 |
| GET/POST | `/api/programs` | 程序保存 / 加载 |
| POST | `/api/tts` | TTS 语音合成 |
| GET | `/api/tcp` | 获取 TCP 位姿 |
| GET | `/audio/<filename>` | 获取 TTS 音频文件 |

### Socket.IO（前端 → 后端）

`run_program` / `stop` / `pause` / `resume` / `step` / `set_joint` / `set_joints` / `nl_input` / `audio_utterance` / `set_suck` / `scene_update` / `clear_chat`

### Socket.IO（后端 → 前端）

`robot_frame` / `program_finished` / `ai_reply` / `asr_status` / `asr_final` / `asr_error` / `scene_objects` / `log`

---

## 机器人参数

- 型号：埃夫特 ER3-600
- 负载：3 kg
- 工作半径：593 mm
- 关节限位：±170°
- 初始位姿：`[0, -90, 90, 0, 0, 0]`

---

## 验证

```bash
cd backend && venv/Scripts/python -m pytest tests -q   # 后端单元测试（35 passed）

# 前端需先在 :3000 运行
node tools/ui_probe.mjs        # 前端端到端（30 项）
node tools/ai_chain_probe.mjs  # AI 全链路（19 项，真实智谱 API）
node tools/console_probe.mjs   # 运行时异常 / 失败请求（应 0）
node tools/ptt_probe.mjs       # 长按空格 push-to-talk（6 项）
node tools/fence_test.mjs      # 安全围栏分级单测（9 项，纯 Node）
node tools/fence_probe.mjs     # 安全围栏端到端（14 项，含截图）
node tools/tts_toggle_probe.mjs # 语音播报开关（10 项：UI + 持久化 + speak 拦截）
```

---

## 注意事项

1. 首次使用语音功能需允许浏览器麦克风权限。
2. 首次语音播报前需点击页面任意位置（浏览器自动播放限制，已做自动补播）。
3. AI 功能可用：后端 `.env` 配 `ZHIPU_API_KEY`（全局演示 Key），或在页面右上角「设置」里填自己的 Key（推荐，按会话隔离、互不串号）。
4. faster-whisper 首次加载模型需经镜像下载（约 500MB），之后缓存。
5. 企业代理环境若无法访问 huggingface.co，设置 `HF_ENDPOINT=https://hf-mirror.com`。
6. Python 推荐 3.11（依赖统一装在 `backend/venv`，不污染全局环境）。
