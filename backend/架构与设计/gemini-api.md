# Gemini API Function Calling 函数调用

函数调用让你可以将模型连接到外部工具和 API。模型不是生成文本响应，而是确定何时调用特定函数，并提供执行实际操作所需的参数。这使得模型可以充当自然语言和实际操作及数据之间的桥梁。

函数调用有 3 个主要用例：

- **增强知识**：从外部数据源（如数据库、API 和知识库）访问信息
- **扩展能力**：使用外部工具执行计算并扩展模型的限制，例如使用计算器或创建图表
- **执行操作**：使用 API 与外部系统交互，例如安排约会、创建发票、发送电子邮件或控制智能家居设备

## 快速示例

```python
from google import genai
from google.genai import types

# 为模型定义函数声明
schedule_meeting_function = {
    "name": "schedule_meeting",
    "description": "安排与指定参与者在给定时间和日期的会议。",
    "parameters": {
        "type": "object",
        "properties": {
            "attendees": {
                "type": "array",
                "items": {"type": "string"},
                "description": "参加会议的人员列表。",
            },
            "date": {
                "type": "string",
                "description": "会议日期（例如，'2024-07-29'）",
            },
            "time": {
                "type": "string",
                "description": "会议时间（例如，'15:00'）",
            },
            "topic": {
                "type": "string",
                "description": "会议的主题或议题。",
            },
        },
        "required": ["attendees", "date", "time", "topic"],
    },
}

# 配置客户端和工具
client = genai.Client()
tools = types.Tool(function_declarations=[schedule_meeting_function])
config = types.GenerateContentConfig(tools=[tools])

# 发送带有函数声明的请求
response = client.models.generate_content(
    model="gemini-3-flash-preview",
    contents="安排一个与 Bob 和 Alice 在 2025年3月14日上午10:00 关于第三季度规划的会议。",
    config=config,
)

# 检查函数调用
if response.candidates[0].content.parts[0].function_call:
    function_call = response.candidates[0].content.parts[0].function_call
    print(f"要调用的函数: {function_call.name}")
    print(f"参数: {function_call.args}")
    # 在实际应用中，你会在这里调用你的函数:
    # result = schedule_meeting(**function_call.args)
else:
    print("响应中未找到函数调用。")
    print(response.text)
```

## 函数调用的工作原理

函数调用涉及应用程序、模型和外部函数之间的结构化交互。以下是该过程的详细说明：

1. **定义函数声明**：在应用程序代码中定义函数声明。函数声明向模型描述函数的名称、参数和目的。

2. **使用函数声明调用 LLM**：将用户提示连同函数声明一起发送到模型。它分析请求并确定函数调用是否有帮助。如果是，它会响应一个结构化的 JSON 对象。

3. **执行函数代码（你的责任）**：模型本身不执行函数。应用程序负责处理响应并检查函数调用：
   - 如果是：提取函数的名称和参数，并在应用程序中执行相应的函数
   - 如果否：模型已提供对提示的直接文本响应

4. **创建用户友好的响应**：如果执行了函数，捕获结果并在对话的后续轮次中将其发送回模型。它将使用结果生成一个最终的、用户友好的响应，其中包含函数调用的信息。

这个过程可以在多个轮次中重复，允许复杂的交互和工作流程。模型还支持在单个轮次中调用多个函数（并行函数调用）和按顺序调用（组合函数调用）。

### 步骤 1：定义函数声明

在应用程序代码中定义一个函数及其声明，允许用户设置灯光值并进行 API 请求。这个函数可以调用外部服务或 API。

```python
# 定义一个模型可以调用来控制智能灯的函数
set_light_values_declaration = {
    "name": "set_light_values",
    "description": "设置灯的亮度和色温。",
    "parameters": {
        "type": "object",
        "properties": {
            "brightness": {
                "type": "integer",
                "description": "灯光级别从 0 到 100。零表示关闭，100 表示全亮",
            },
            "color_temp": {
                "type": "string",
                "enum": ["daylight", "cool", "warm"],
                "description": "灯具的色温，可以是 `daylight`、`cool` 或 `warm`。",
            },
        },
        "required": ["brightness", "color_temp"],
    },
}

# 这是基于模型建议将被调用的实际函数
def set_light_values(brightness: int, color_temp: str) -> dict[str, int | str]:
    """设置房间灯的亮度和色温。（模拟 API）

    Args:
        brightness: 灯光级别从 0 到 100。零表示关闭，100 表示全亮
        color_temp: 灯具的色温，可以是 `daylight`、`cool` 或 `warm`。

    Returns:
        包含设置的亮度和色温的字典。
    """
    return {"brightness": brightness, "colorTemperature": color_temp}
```

### 步骤 2：使用函数声明调用模型

定义函数声明后，你可以提示模型使用它们。它分析提示和函数声明，并决定是直接响应还是调用函数。如果调用了函数，响应对象将包含函数调用建议。

```python
from google.genai import types

# 配置客户端和工具
client = genai.Client()
tools = types.Tool(function_declarations=[set_light_values_declaration])
config = types.GenerateContentConfig(tools=[tools])

# 定义用户提示
contents = [
    types.Content(
        role="user", parts=[types.Part(text="把灯光调暗到浪漫的程度")]
    )
]

# 发送带有函数声明的请求
response = client.models.generate_content(
    model="gemini-3-flash-preview",
    contents=contents,
    config=config,
)

print(response.candidates[0].content.parts[0].function_call)
```

然后模型返回一个符合 OpenAPI 兼容模式的 `functionCall` 对象，指定如何调用一个或多个声明的函数以响应用户的问题。

```python
id=None args={'color_temp': 'warm', 'brightness': 25} name='set_light_values'
```

### 步骤 3：执行 set_light_values 函数代码

从模型的响应中提取函数调用详细信息，解析参数，并执行 `set_light_values` 函数。

```python
# 提取工具调用详细信息，它可能不在第一部分
tool_call = response.candidates[0].content.parts[0].function_call

if tool_call.name == "set_light_values":
    result = set_light_values(**tool_call.args)
    print(f"函数执行结果: {result}")
```

### 步骤 4：使用函数结果创建用户友好的响应并再次调用模型

最后，将函数执行的结果发送回模型，以便它可以将这些信息整合到对用户的最终响应中。

```python
from google import genai
from google.genai import types

# 创建函数响应部分
function_response_part = types.Part.from_function_response(
    name=tool_call.name,
    response={"result": result},
)

# 将函数调用和函数执行结果附加到内容
contents.append(response.candidates[0].content) # 附加模型响应的内容
contents.append(types.Content(role="user", parts=[function_response_part])) # 附加函数响应

client = genai.Client()
final_response = client.models.generate_content(
    model="gemini-3-flash-preview",
    config=config,
    contents=contents,
)

print(final_response.text)
```

这完成了函数调用流程。模型成功使用 `set_light_values` 函数执行了用户的请求操作。

## 函数声明

当你在提示中实现函数调用时，你创建一个 `tools` 对象，它包含一个或多个函数声明。你使用 JSON 定义函数，特别是使用 OpenAPI 模式格式的选定子集。单个函数声明可以包含以下参数：

- **name**（字符串）：函数的唯一名称（`get_weather_forecast`、`send_email`）。使用描述性名称，不带空格或特殊字符（使用下划线或驼峰命名法）。

- **description**（字符串）：对函数目的和功能的清晰详细说明。这对于模型理解何时使用该函数至关重要。要具体，如果有帮助，请提供示例（"根据位置查找影院，可选电影标题，该电影当前正在影院上映。"）。

- **parameters**（对象）：定义函数期望的输入参数。
  - **type**（字符串）：指定整体数据类型，例如 `object`。
  - **properties**（对象）：列出各个参数，每个参数具有：
    - **type**（字符串）：参数的数据类型，例如 `string`、`integer`、`boolean`、`array`。
    - **description**（字符串）：参数目的和格式的描述。提供示例和约束（"城市和州，例如 'San Francisco, CA' 或邮政编码，例如 '95616'。"）。
    - **enum**（数组，可选）：如果参数值来自固定集合，使用 "enum" 列出允许的值，而不仅仅是在描述中描述它们。这提高了准确性（`"enum": ["daylight", "cool", "warm"]`）。
  - **required**（数组）：字符串数组，列出函数运行所必需的参数名称。

你还可以使用 `types.FunctionDeclaration.from_callable(client=client, callable=your_function)` 直接从 Python 函数构造 `FunctionDeclarations`。

## 思考模型的函数调用

Gemini 3 和 2.5 系列模型使用内部"思考"过程来推理请求。这显著提高了函数调用性能，使模型能够更好地确定何时调用函数以及使用哪些参数。由于 Gemini API 是无状态的，模型使用思想签名在多轮对话中维护上下文。

本节涵盖思想签名的高级管理，仅在你手动构造 API 请求（例如，通过 REST）或操作对话历史记录时才需要。

如果你使用 Google GenAI SDK（我们的官方库），则无需管理此过程。SDK 会自动处理必要的步骤，如前面的示例所示。

### 手动管理对话历史记录

如果你手动修改对话历史记录，而不是发送完整的先前响应，你必须正确处理模型轮次中包含的 `thought_signature`。

遵循以下规则以确保保留模型的上下文：

- 始终将 `thought_signature` 发送回模型内部的原始 `Part`。
- 不要将包含签名的 `Part` 与不包含签名的 `Part` 合并。这会破坏思想的位置上下文。
- 不要合并两个都包含签名的 `Part`，因为签名字符串无法合并。

### Gemini 3 思想签名

在 Gemini 3 中，模型响应的任何 `Part` 都可能包含思想签名。

虽然我们通常建议从所有 `Part` 类型返回签名，但对于函数调用，传递回思想签名是强制性的。除非你手动操作对话历史记录，否则 Google GenAI SDK 将自动处理思想签名。

如果你手动操作对话历史记录，请参阅思想签名页面以获取有关处理 Gemini 3 思想签名的完整指导和详细信息。

### 检查思想签名

虽然对于实现不是必需的，但你可以检查响应以查看 `thought_signature`，用于调试或教育目的。

```python
import base64
# 从启用思考的模型接收响应后
# response = client.models.generate_content(...)

# 签名附加到包含函数调用的响应部分
part = response.candidates[0].content.parts[0]
if part.thought_signature:
    print(base64.b64encode(part.thought_signature).decode("utf-8"))
```

了解更多关于思想签名的限制和使用，以及关于思考模型的一般信息，请访问思考页面。

## 并行函数调用

除了单轮函数调用外，你还可以一次调用多个函数。并行函数调用允许你一次执行多个函数，当函数彼此不依赖时使用。这在从多个独立来源收集数据的场景中很有用，例如从不同数据库检索客户详细信息或检查各个仓库的库存水平，或执行多个操作，例如将你的公寓变成迪斯科舞厅。

```python
power_disco_ball = {
    "name": "power_disco_ball",
    "description": "启动旋转迪斯科球。",
    "parameters": {
        "type": "object",
        "properties": {
            "power": {
                "type": "boolean",
                "description": "是否打开或关闭迪斯科球。",
            }
        },
        "required": ["power"],
    },
}

start_music = {
    "name": "start_music",
    "description": "播放符合指定参数的音乐。",
    "parameters": {
        "type": "object",
        "properties": {
            "energetic": {
                "type": "boolean",
                "description": "音乐是否充满活力。",
            },
            "loud": {
                "type": "boolean",
                "description": "音乐是否响亮。",
            },
        },
        "required": ["energetic", "loud"],
    },
}

dim_lights = {
    "name": "dim_lights",
    "description": "调暗灯光。",
    "parameters": {
        "type": "object",
        "properties": {
            "brightness": {
                "type": "number",
                "description": "灯的亮度，0.0 表示关闭，1.0 表示全亮。",
            }
        },
        "required": ["brightness"],
    },
}
```

配置函数调用模式以允许使用所有指定的工具。要了解更多信息，你可以阅读有关配置函数调用的内容。

```python
from google import genai
from google.genai import types

# 配置客户端和工具
client = genai.Client()
house_tools = [
    types.Tool(function_declarations=[power_disco_ball, start_music, dim_lights])
]
config = types.GenerateContentConfig(
    tools=house_tools,
    automatic_function_calling=types.AutomaticFunctionCallingConfig(
        disable=True
    ),
    # 强制模型调用'任何'函数，而不是聊天
    tool_config=types.ToolConfig(
        function_calling_config=types.FunctionCallingConfig(mode='ANY')
    ),
)

chat = client.chats.create(model="gemini-3-flash-preview", config=config)
response = chat.send_message("把这个地方变成派对！")

# 打印此单次调用请求的每个函数调用
print("示例 1: 强制函数调用")
for fn in response.function_calls:
    args = ", ".join(f"{key}={val}" for key, val in fn.args.items())
    print(f"{fn.name}({args})")
```

打印的每个结果都反映了模型请求的单个函数调用。要发送结果回去，请按照请求的相同顺序包含响应。

Python SDK 支持自动函数调用，它会自动将 Python 函数转换为声明，为你处理函数调用执行和响应循环。以下是迪斯科用例的示例。

注意：自动函数调用目前仅是 Python SDK 功能。

```python
from google import genai
from google.genai import types

# 实际函数实现
def power_disco_ball_impl(power: bool) -> dict:
    """启动旋转迪斯科球。

    Args:
        power: 是否打开或关闭迪斯科球。

    Returns:
        指示当前状态的状态字典。
    """
    return {"status": f"迪斯科球已{'打开' if power else '关闭'}"}

def start_music_impl(energetic: bool, loud: bool) -> dict:
    """播放符合指定参数的音乐。

    Args:
        energetic: 音乐是否充满活力。
        loud: 音乐是否响亮。

    Returns:
        包含音乐设置的字典。
    """
    music_type = "充满活力" if energetic else "轻松"
    volume = "响亮" if loud else "安静"
    return {"music_type": music_type, "volume": volume}

def dim_lights_impl(brightness: float) -> dict:
    """调暗灯光。

    Args:
        brightness: 灯的亮度，0.0 表示关闭，1.0 表示全亮。

    Returns:
        包含新亮度设置的字典。
    """
    return {"brightness": brightness}

# 配置客户端
client = genai.Client()
config = types.GenerateContentConfig(
    tools=[power_disco_ball_impl, start_music_impl, dim_lights_impl]
)

# 发出请求
response = client.models.generate_content(
    model="gemini-3-flash-preview",
    contents="做所有你需要做的事情把这个地方变成派对！",
    config=config,
)

print("\n示例 2: 自动函数调用")
print(response.text)
# 我已经打开了迪斯科球，开始播放响亮而充满活力的音乐，并将灯光调暗到 50% 的亮度。让我们开始派对吧！
```

## 组合函数调用

组合或顺序函数调用允许 Gemini 将多个函数调用链接在一起以完成复杂的请求。例如，要回答"获取我当前位置的温度"，Gemini API 可能首先调用 `get_current_location()` 函数，然后调用以位置为参数的 `get_weather()` 函数。

以下示例演示如何使用 Python SDK 和自动函数调用实现组合函数调用。

此示例使用 `google-genai` Python SDK 的自动函数调用功能。SDK 会自动将 Python 函数转换为所需的模式，在模型请求时执行函数调用，并将结果发送回模型以完成任务。

```python
import os
from google import genai
from google.genai import types

# 示例函数
def get_weather_forecast(location: str) -> dict:
    """获取给定位置的当前天气温度。"""
    print(f"工具调用: get_weather_forecast(location={location})")
    # TODO: 进行 API 调用
    print("工具响应: {'temperature': 25, 'unit': 'celsius'}")
    return {"temperature": 25, "unit": "celsius"}  # 虚拟响应

def set_thermostat_temperature(temperature: int) -> dict:
    """将恒温器设置为所需温度。"""
    print(f"工具调用: set_thermostat_temperature(temperature={temperature})")
    # TODO: 与恒温器 API 交互
    print("工具响应: {'status': 'success'}")
    return {"status": "success"}

# 配置客户端和模型
client = genai.Client()
config = types.GenerateContentConfig(
    tools=[get_weather_forecast, set_thermostat_temperature]
)

# 发出请求
response = client.models.generate_content(
    model="gemini-3-flash-preview",
    contents="如果伦敦的温度高于 20°C，将恒温器设置为 20°C，否则设置为 18°C。",
    config=config,
)

# 打印最终的用户友好响应
print(response.text)
```

### 预期输出

当你运行代码时，你将看到 SDK 编排函数调用。模型首先调用 `get_weather_forecast`，接收温度，然后根据提示中的逻辑使用正确的值调用 `set_thermostat_temperature`。

```
工具调用: get_weather_forecast(location=London)
工具响应: {'temperature': 25, 'unit': 'celsius'}
工具调用: set_thermostat_temperature(temperature=20)
工具响应: {'status': 'success'}
好的。我已将恒温器设置为 20°C。
```

组合函数调用是 Live API 的原生功能。这意味着 Live API 可以类似于 Python SDK 处理函数调用。

```python
# 灯光控制模式
turn_on_the_lights_schema = {'name': 'turn_on_the_lights'}
turn_off_the_lights_schema = {'name': 'turn_off_the_lights'}

prompt = """
  嘿，你能为我做三件事吗？

    1. 打开灯。
    2. 然后计算 100000 以下最大的回文质数。
    3. 然后使用 Google 搜索查找有关 2024 年 12 月 5 日那周加利福尼亚州最大地震的信息。

  谢谢！
  """

tools = [
    {'google_search': {}},
    {'code_execution': {}},
    {'function_declarations': [turn_on_the_lights_schema, turn_off_the_lights_schema]}
]

# 以音频模式执行带有指定工具的提示
await run(prompt, tools=tools, modality="AUDIO")
```

## 函数调用模式

Gemini API 允许你控制模型如何使用提供的工具（函数声明）。具体来说，你可以在 `.function_calling_config` 中设置模式。

- **AUTO**（默认）：模型根据提示和上下文决定是生成自然语言响应还是建议函数调用。这是最灵活的模式，建议用于大多数场景。

- **ANY**：模型被约束为始终预测函数调用并保证函数模式遵守。如果未指定 `allowed_function_names`，模型可以从任何提供的函数声明中选择。如果提供 `allowed_function_names` 作为列表，模型只能从该列表中的函数中选择。当你需要对每个提示的函数调用响应时（如果适用），请使用此模式。

- **NONE**：禁止模型进行函数调用。这相当于发送不带任何函数声明的请求。使用此选项可以在不删除工具定义的情况下临时禁用函数调用。

- **VALIDATED**（预览）：模型被约束为预测函数调用或自然语言，并确保函数模式遵守。如果未提供 `allowed_function_names`，模型从所有可用的函数声明中选择。如果提供了 `allowed_function_names`，模型从允许的函数集中选择。

```python
from google.genai import types

# 配置函数调用模式
tool_config = types.ToolConfig(
    function_calling_config=types.FunctionCallingConfig(
        mode="ANY", allowed_function_names=["get_current_temperature"]
    )
)

# 创建生成配置
config = types.GenerateContentConfig(
    tools=[tools],  # 此处未定义
    tool_config=tool_config,
)
```

## 自动函数调用（仅限 Python）

使用 Python SDK 时，你可以直接提供 Python 函数作为工具。SDK 将这些函数转换为声明，管理函数调用执行，并为你处理响应循环。使用类型提示和文档字符串定义你的函数。为获得最佳结果，建议使用 Google 风格的文档字符串。

然后 SDK 将自动：

- 检测模型的函数调用响应
- 在你的代码中调用相应的 Python 函数
- 将函数的响应发送回模型
- 返回模型的最终文本响应

SDK 目前不会将参数描述解析到生成的函数声明的属性描述槽中。相反，它将整个文档字符串作为顶级函数描述发送。

```python
from google import genai
from google.genai import types

# 使用类型提示和文档字符串定义函数
def get_current_temperature(location: str) -> dict:
    """获取给定位置的当前温度。

    Args:
        location: 城市和州，例如 San Francisco, CA

    Returns:
        包含温度和单位的字典。
    """
    # ...（实现）...
    return {"temperature": 25, "unit": "Celsius"}

# 配置客户端
client = genai.Client()
config = types.GenerateContentConfig(
    tools=[get_current_temperature]
)  # 传递函数本身

# 发出请求
response = client.models.generate_content(
    model="gemini-3-flash-preview",
    contents="波士顿的温度是多少？",
    config=config,
)

print(response.text)  # SDK 处理函数调用并返回最终文本
```

你可以使用以下方式禁用自动函数调用：

```python
config = types.GenerateContentConfig(
    tools=[get_current_temperature],
    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)
)
```

### 自动函数模式声明

API 能够描述以下任何类型。Pydantic 类型是允许的，只要在它们上定义的字段也由允许的类型组成。不支持字典类型（如 `dict[str: int]`），请勿使用它们。

```python
AllowedType = (
    int | float | bool | str | list['AllowedType'] | pydantic.BaseModel)
```

要查看推断的模式是什么样子，你可以使用 `from_callable` 进行转换：

```python
from google import genai
from google.genai import types

def multiply(a: float, b: float):
    """返回 a * b。"""
    return a * b

client = genai.Client()
fn_decl = types.FunctionDeclaration.from_callable(callable=multiply, client=client)

# to_json_dict() 提供一个干净的 JSON 表示
print(fn_decl.to_json_dict())
```

## 多工具使用：将原生工具与函数调用结合

你可以在请求中同时启用多个工具，将原生工具与函数调用结合使用。以下是一个在使用 Live API 的请求中启用两个工具的示例，即使用 Google 搜索的 Grounding 和代码执行。

注意：多工具使用目前仅限于 Live API 功能。为简洁起见，省略了处理异步 websocket 设置的 `run()` 函数声明。

```python
# 多任务示例 - 结合灯光、代码执行和搜索
prompt = """
  嘿，我需要你为我做三件事。

    1. 打开灯。
    2. 然后计算 100000 以下最大的回文质数。
    3. 然后使用 Google 搜索查找有关 2024 年 12 月 5 日那周加利福尼亚州最大地震的信息。

  谢谢！
  """

tools = [
    {'google_search': {}},
    {'code_execution': {}},
    {'function_declarations': [turn_on_the_lights_schema, turn_off_the_lights_schema]} # 此处未定义
]

# 以音频模式使用指定工具执行提示
await run(prompt, tools=tools, modality="AUDIO")
```

Python 开发人员可以在 Live API 工具使用笔记本中尝试这个。

## 多模态函数响应

注意：此功能适用于 Gemini 3 系列模型。

对于 Gemini 3 系列模型，你可以在发送到模型的函数响应部分中包含多模态内容。模型可以在下一轮中处理此多模态内容以产生更明智的响应。

函数响应中的多模态内容支持以下 MIME 类型：

- 图像：`image/png`、`image/jpeg`、`image/webp`
- 文档：`application/pdf`、`text/plain`

要在函数响应中包含多模态数据，请将其作为一个或多个部分嵌套在 `functionResponse` 部分中。每个多模态部分必须包含 `inlineData`。如果你从结构化响应字段中引用多模态部分，它必须包含唯一的 `displayName`。

你还可以通过使用 JSON 引用格式 `{"$ref": "<displayName>"}` 从 `functionResponse` 部分的结构化响应字段中引用多模态部分。模型在处理响应时用多模态内容替换引用。每个 `displayName` 只能在结构化响应字段中引用一次。

以下示例显示一条消息，其中包含名为 `get_image` 的函数的 `functionResponse`，以及一个嵌套部分，其中包含带有 `displayName: "instrument.jpg"` 的图像数据。`functionResponse` 的 `response` 字段引用此图像部分：

```python
from google import genai
from google.genai import types

import requests

client = genai.Client()

# 这是一个手动的、两轮多模态函数调用工作流程：

# 1. 定义函数工具
get_image_declaration = types.FunctionDeclaration(
    name="get_image",
    description="检索特定订单项目的图像文件引用。",
    parameters={
        "type": "object",
        "properties": {
            "item_name": {
                "type": "string",
                "description": "订购的物品的名称或描述（例如，'instrument'）。"
            }
        },
        "required": ["item_name"],
    },
)
tool_config = types.Tool(function_declarations=[get_image_declaration])

# 2. 发送触发工具的消息
prompt = "显示我上个月订购的乐器。"
response_1 = client.models.generate_content(
    model="gemini-3-flash-preview",
    contents=[prompt],
    config=types.GenerateContentConfig(
        tools=[tool_config],
    )
)

# 3. 处理函数调用
function_call = response_1.function_calls[0]
requested_item = function_call.args["item_name"]
print(f"模型想要调用: {function_call.name}")

# 执行你的工具（例如，调用 API）
# （这是示例的模拟响应）
print(f"为以下内容调用外部工具: {requested_item}")

function_response_data = {
    "image_ref": {"$ref": "instrument.jpg"},
}
image_path = "https://goo.gle/instrument-img"
image_bytes = requests.get(image_path).content
function_response_multimodal_data = types.FunctionResponsePart(
    inline_data=types.FunctionResponseBlob(
        mime_type="image/jpeg",
        display_name="instrument.jpg",
        data=image_bytes,
    )
)

# 4. 将工具的结果发送回去
# 将此轮的消息附加到历史记录以获得最终响应
history = [
    types.Content(role="user", parts=[types.Part(text=prompt)]),
    response_1.candidates[0].content,
    types.Content(
        role="tool",
        parts=[
            types.Part.from_function_response(
                name=function_call.name,
                response=function_response_data,
                parts=[function_response_multimodal_data]
            )
        ],
    )
]

response_2 = client.models.generate_content(
    model="gemini-3-flash-preview",
    contents=history,
    config=types.GenerateContentConfig(
        tools=[tool_config],
        thinking_config=types.ThinkingConfig(include_thoughts=True)
    ),
)

print(f"\n最终模型响应: {response_2.text}")
```

## 模型上下文协议（MCP）

模型上下文协议（MCP）是一个将 AI 应用程序与外部工具和数据连接的开放标准。MCP 为模型提供了一个通用协议来访问上下文，例如函数（工具）、数据源（资源）或预定义提示。

Gemini SDK 内置了对 MCP 的支持，减少了样板代码并为 MCP 工具提供了自动工具调用。当模型生成 MCP 工具调用时，Python 和 JavaScript 客户端 SDK 可以自动执行 MCP 工具并在后续请求中将响应发送回模型，继续此循环直到模型不再进行工具调用。

以下是如何在 Gemini 和 mcp SDK 中使用本地 MCP 服务器的示例。

确保在你选择的平台上安装了最新版本的 mcp SDK。

```bash
pip install mcp
```

注意：Python 通过将 `ClientSession` 传递到 `tools` 参数中来支持自动工具调用。如果要禁用它，可以提供 `automatic_function_calling` 并将 `disable` 设置为 `True`。

```python
import os
import asyncio
from datetime import datetime
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from google import genai

client = genai.Client()

# 为 stdio 连接创建服务器参数
server_params = StdioServerParameters(
    command="npx",  # 可执行文件
    args=["-y", "@philschmid/weather-mcp"],  # MCP 服务器
    env=None,  # 可选环境变量
)

async def run():
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            # 提示获取伦敦当天的天气
            prompt = f"伦敦在 {datetime.now().strftime('%Y-%m-%d')} 的天气如何？"

            # 初始化客户端和服务器之间的连接
            await session.initialize()

            # 使用 MCP 函数声明向模型发送请求
            response = await client.aio.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=genai.types.GenerateContentConfig(
                    temperature=0,
                    tools=[session],  # 使用会话，将自动调用工具
                    # 如果你**不想** SDK 自动调用工具，请取消注释
                    # automatic_function_calling=genai.types.AutomaticFunctionCallingConfig(
                    #     disable=True
                    # ),
                ),
            )
            print(response.text)

# 启动 asyncio 事件循环并运行主函数
asyncio.run(run())
```

### 内置 MCP 支持的限制

内置 MCP 支持是我们 SDK 中的实验性功能，具有以下限制：

- 仅支持工具，不支持资源或提示
- 它适用于 Python 和 JavaScript/TypeScript SDK
- 未来版本中可能会发生破坏性更改

如果这些限制了你正在构建的内容，手动集成 MCP 服务器始终是一个选项。

## 支持的模型

本节列出了模型及其函数调用能力。不包括实验性模型。你可以在模型概述页面上找到全面的能力概述。

| 模型 | 函数调用 | 并行函数调用 | 组合函数调用 |
|------|---------|------------|-------------|
| Gemini 3 Pro | ✔️ | ✔️ | ✔️ |
| Gemini 3 Flash | ✔️ | ✔️ | ✔️ |
| Gemini 2.5 Pro | ✔️ | ✔️ | ✔️ |
| Gemini 2.5 Flash | ✔️ | ✔️ | ✔️ |
| Gemini 2.5 Flash-Lite | ✔️ | ✔️ | ✔️ |
| Gemini 2.0 Flash | ✔️ | ✔️ | ✔️ |
| Gemini 2.0 Flash-Lite | ❌ | ❌ | ❌ |

## 最佳实践

1. **函数和参数描述**：在描述中要极其清晰和具体。模型依赖这些来选择正确的函数并提供适当的参数。

2. **命名**：使用描述性的函数名称（不带空格、句点或破折号）。

3. **强类型**：对参数使用特定类型（`integer`、`string`、`enum`）以减少错误。如果参数有一组有限的有效值，请使用 `enum`。

4. **工具选择**：虽然模型可以使用任意数量的工具，但提供太多工具会增加选择不正确或次优工具的风险。为获得最佳结果，旨在仅提供与上下文或任务相关的工具，理想情况下将活动集保持在最多 10-20 个。如果你有大量工具，请考虑基于对话上下文的动态工具选择。

5. **提示工程**：
   - 提供上下文：告诉模型它的角色（例如，"你是一个有用的天气助手。"）
   - 给出指示：指定如何以及何时使用函数（例如，"不要猜测日期；始终使用未来日期进行预测。"）
   - 鼓励澄清：指示模型在需要时提出澄清问题

   请参阅代理工作流程以了解设计这些提示的进一步策略。以下是测试系统指令的示例。

6. **温度**：使用低温度（例如，0）以获得更确定和可靠的函数调用。

   使用 Gemini 3 模型时，我们强烈建议将温度保持在默认值 1.0。更改温度（将其设置为 1.0 以下）可能会导致意外行为，例如循环或性能下降，特别是在复杂的数学或推理任务中。

7. **验证**：如果函数调用具有重大后果（例如，下订单），请在执行之前与用户验证调用。

8. **检查完成原因**：始终检查模型响应中的 `finishReason` 以处理模型未能生成有效函数调用的情况。

9. **错误处理**：在函数中实现强大的错误处理，以优雅地处理意外输入或 API 故障。返回模型可以用来生成有用的用户响应的信息性错误消息。

10. **安全性**：在调用外部 API 时要注意安全性。使用适当的身份验证和授权机制。避免在函数调用中暴露敏感数据。

11. **令牌限制**：函数描述和参数计入你的输入令牌限制。如果你遇到令牌限制，请考虑限制函数数量或描述长度，将复杂任务分解为更小、更集中的函数集。

## 注意事项和限制

- 仅支持 OpenAPI 模式的子集
- Python 中支持的参数类型有限
- 自动函数调用仅是 Python SDK 功能
