/**
 * ollama.mjs - Ollama AI Provider 实现
 * 
 * 使用 Ollama /api/chat 接口发送系统与用户消息，获取解析后的 JSON 元数据。
 */

import { BaseProvider } from './base.mjs';

export class OllamaProvider extends BaseProvider {
  constructor(config = {}) {
    super(config);
    this.baseUrl = (config.baseUrl || 'http://localhost:11434').replace(/\/$/, '');
    this.model = config.model || 'qwen3.5:9b';
    this.temperature = config.temperature ?? 0.1;
  }

  get id() {
    return 'ollama';
  }

  async healthCheck() {
    try {
      const resp = await fetch(`${this.baseUrl}/api/tags`, {
        signal: AbortSignal.timeout(5000)
      });
      return resp.ok;
    } catch {
      return false;
    }
  }

  async extractMetadata(markdownContent, options = {}) {
    const systemPrompt = options.systemPrompt || 'You are an education resource metadata extractor. Return only valid JSON.';
    
    const body = {
      model: this.model,
      messages: [
        { role: 'system', content: systemPrompt },
        { role: 'user', content: markdownContent }
      ],
      stream: false,
      think: false,   // 禁用思考模式（当前 Luceon 生产链路需要稳定 JSON，禁止 thinking 输出污染业务解析）
      options: {
        think: false, // 不允许由外部 settings 覆盖
        temperature: options.temperature ?? this.temperature,
        top_p: options.top_p,
        num_predict: options.num_predict || 1024
      }
    };

    if (options.expectJson !== false) {
      body.format = 'json'; // 强制 Ollama 输出 JSON
    }

    const startTime = Date.now();
    try {
      const response = await fetch(`${this.baseUrl}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(this.timeoutMs)
      });

      if (!response.ok) {
        const errorText = await response.text();
        throw new Error(`Ollama API error: HTTP ${response.status} - ${errorText}`);
      }

      const data = await response.json();
      const rawContent = data.message?.content || '';
      const rawContainsThinkTag = rawContent.includes('<think>');
      const strippedContent = this.filterThinking(rawContent);
      const duration = Date.now() - startTime;

      let result;
      if (options.expectJson === false) {
        result = strippedContent;
      } else {
        result = this.parseJsonRobust(strippedContent);
        if (!result) {
          if (options.returnRawOnParseFailure) {
            return {
              result: strippedContent,
              rawResponse: strippedContent,
              rawContainsThinkTag,
              parseFailed: true,
              parseError: `Failed to parse JSON from Ollama response, model: ${this.model}`,
              usage: {
                total_duration_ms: duration,
                prompt_tokens: data.prompt_eval_count || 0,
                completion_tokens: data.eval_count || 0
              },
              provider: this.id,
              model: this.model
            };
          }

          const parseErr = new Error(`Failed to parse JSON from Ollama response, model: ${this.model}`);
          const rawTrimmed = strippedContent.trim();
          parseErr.rawContentDetails = {
            rawContentPreview: strippedContent.slice(0, 1000),
            rawContentLength: strippedContent.length,
            rawContentHead: strippedContent.slice(0, 300),
            rawContentTail: strippedContent.slice(-300),
            rawLooksTruncated: strippedContent.includes('{') && !rawTrimmed.endsWith('}') && !rawTrimmed.endsWith(']'),
            rawContainsThinkTag,
            responseFormatRequested: options.expectJson !== false,
            expectJson: options.expectJson,
            parseErrorMessage: parseErr.message
          };
          throw parseErr;
        }
      }

      return {
        result,
        rawResponse: strippedContent,
        rawContainsThinkTag,
        usage: {
          total_duration_ms: duration,
          prompt_tokens: data.prompt_eval_count || 0,
          completion_tokens: data.eval_count || 0
        },
        provider: this.id,
        model: this.model
      };
    } catch (err) {
      const duration = Date.now() - startTime;
      const errorDetail = {
        name: err.name,
        message: err.message,
        cause: err.cause ? { code: err.cause.code, message: err.cause.message } : null,
        baseUrl: this.baseUrl,
        model: this.model,
        timeoutMs: this.timeoutMs,
        durationMs: duration,
        ...(err.rawContentDetails || {})
      };
      
      const detailedMessage = `Ollama Provider Error: [${err.name}] ${err.message} (BaseURL: ${this.baseUrl}, Model: ${this.model}, Duration: ${duration}ms, Timeout: ${this.timeoutMs}ms)`;
      
      const error = new Error(detailedMessage);
      error.details = errorDetail;
      throw error;
    }
  }
}
