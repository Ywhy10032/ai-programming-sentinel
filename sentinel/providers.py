"""客户端目录只描述接入能力，不把进程存在当作任务状态。"""
PROVIDERS = {
    'codex': ('Codex', 'CX', '#e5eeee', '日志'),
    'claude': ('Claude', 'CL', '#db9773', '回调 / 界面'),
    'chatgpt': ('ChatGPT', 'GPT', '#61c5aa', '界面'),
    'deepseek': ('DeepSeek Harness', 'DS', '#6394ff', '会话事件 / 界面'),
    'zcode': ('ZCode', 'Z', '#79afff', '回调 / 界面'),
    'cursor': ('Cursor', 'CU', '#dce6ec', '回调 / 界面'),
    'windsurf': ('Windsurf / Devin Desktop', 'WS', '#6fd6bd', '回调 / 界面'),
    'trae': ('Trae', 'TR', '#6de1b3', '界面'),
    'cherry': ('Cherry Studio', 'CH', '#ee8295', '界面'),
    'lmstudio': ('LM Studio', 'LM', '#b3a0f6', '界面'),
    'jan': ('Jan', 'JAN', '#bfa7fa', '界面'),
    'qwen': ('千问 / Qwen', 'QW', '#a693fa', '界面'),
    'kimi': ('Kimi', 'K', '#c7defe', '界面'),
    'doubao': ('豆包', 'DB', '#95d7fc', '界面'),
    'gemini': ('Gemini', 'GM', '#8cadfa', '通用事件 / 界面'),
    'poe': ('Poe', 'POE', '#b2a3f8', '界面'),
    'perplexity': ('Perplexity', 'PX', '#70c5cc', '界面'),
    'grok': ('Grok', 'GK', '#dddfe7', '界面'),
}


def provider_name(provider):
    return PROVIDERS.get(provider, ('AI 客户端', '', '', '通用事件'))[0]
