/**
 * openai-compatible.mjs - OpenAI 兼容接口 Provider 实现
 * 
 * 用于连接 DeepSeek, OpenAI, 百度千帆等兼容 OpenAI 格式的端点。
 */

import { BaseProvider } from './base.mjs';

export class OpenAiCompatibleProvider extends BaseProvider {
  constructor(config = {}) {
    super(config);
    this.baseUrl = (config.baseUrl || 'https://api.openai.com/v1').replace(/\/$/, '');
    this.model = config.model || 'gpt-3.5-turbo';
    this.apiKey = config.apiKey || '';
    this.temperature = config.temperature ?? 0.1;
    this._providerIdOverride = config.providerIdOverride;
  }

  get id() {
    return this._providerIdOverride || 'openai-compatible';
  }

  async healthCheck() {
    try {
      // 简单通过获取模型列表接口进行健康检查
      const resp = await fetch(`${this.baseUrl}/models`, {
        headers: { Authorization: `Bearer ${this.apiKey}` },
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
      temperature: options.temperature ?? this.temperature,
    };

    if (options.expectJson !== false) {
      body.response_format = { type: 'json_object' }; // 部分端点支持强校验
    }

    const startTime = Date.now();
    const response = await fetch(`${this.baseUrl}/chat/completions`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${this.apiKey}`
      },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(this.timeoutMs)
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`OpenAI-compatible API error: HTTP ${response.status} - ${errorText}`);
    }

    const data = await response.json();
    const rawContent = data.choices?.[0]?.message?.content || '';
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
            parseError: 'Failed to parse JSON from OpenAI-compatible response',
            usage: {
              total_duration_ms: duration,
              prompt_tokens: data.usage?.prompt_tokens || 0,
              completion_tokens: data.usage?.completion_tokens || 0
            },
            provider: this.id,
            model: this.model
          };
        }

        const parseErr = new Error('Failed to parse JSON from OpenAI-compatible response');
        const rawTrimmed = strippedContent.trim();
        parseErr.details = {
          baseUrl: this.baseUrl,
          model: this.model,
          timeoutMs: this.timeoutMs,
          durationMs: duration,
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
        prompt_tokens: data.usage?.prompt_tokens || 0,
        completion_tokens: data.usage?.completion_tokens || 0
      },
      provider: this.id,
      model: this.model
    };
  }
}
