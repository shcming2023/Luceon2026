/**
 * base.mjs - AI Provider 抽象基类
 * 
 * 定义所有 AI 元数据提取提供者必须遵循的契约。
 */

export class BaseProvider {
  /**
   * @param {object} config - 配置信息（端点、模型、API Key、超时等）
   */
  constructor(config = {}) {
    this.config = config;
    this.timeoutMs = Number(config.timeoutMs || 120000); // 默认 120s
  }

  /**
   * 核心识别方法
   * @param {string} markdownContent - 输入的 Markdown 内容
   * @param {object} options - 额外选项（系统提示词、温度等）
   * @returns {Promise<{result: object, rawResponse: string, usage: object, provider: string, model: string}>}
   */
  async extractMetadata(markdownContent, options = {}) {
    throw new Error('Method extractMetadata() must be implemented');
  }

  /**
   * 健康检查
   * @returns {Promise<boolean>}
   */
  async healthCheck() {
    throw new Error('Method healthCheck() must be implemented');
  }

  /**
   * 获取 Provider 唯一标识
   */
  get id() {
    throw new Error('Getter id must be implemented');
  }

  /**
   * 过滤 Qwen 等模型输出中的 <think> 标签内容
   * @param {string} text 
   * @returns {string}
   */
  filterThinking(text) {
    if (!text) return '';
    return text.replace(/<think>[\s\S]*?<\/think>/g, '').trim();
  }

  /**
   * 容错解析 JSON（PRD 10.5.2）
   * @param {string} text 
   * @returns {object|null}
   */
  parseJsonRobust(text) {
    let cleaned = this.filterThinking(text);
    if (!cleaned) return null;

    // 1. 尝试直接解析
    try {
      return JSON.parse(cleaned);
    } catch {}

    // 2. 尝试提取 ```json ... ``` 或 ``` ... ``` 甚至只是不带标签的 code fence
    const mdJsonMatch = cleaned.match(/```(?:json)?\s*([\s\S]*?)\s*```/);
    if (mdJsonMatch && mdJsonMatch[1]) {
      try {
        return JSON.parse(mdJsonMatch[1].trim());
      } catch {}
    }

    // 3. 寻找第一个 { 和最后一个 }
    const startIndex = cleaned.indexOf('{');
    const endIndex = cleaned.lastIndexOf('}');
    if (startIndex !== -1 && endIndex !== -1 && endIndex >= startIndex) {
      const jsonStr = cleaned.slice(startIndex, endIndex + 1);
      try {
        return JSON.parse(jsonStr);
      } catch {}
    }
    
    return null;
  }
}
