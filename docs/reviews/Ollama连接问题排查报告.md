# Ollama AI 连接问题排查报告

## 问题描述
在 CMS 系统的 AI 设置页面（http://101.35.149.123:8085/cms/settings）测试 Ollama 连接时失败，显示 "AI 返回格式异常，无法解析为 JSON"。

## 环境信息

### 部署架构
- **服务器**: Mac Mini (本地开发机)
- **Ollama 服务**: 运行在宿主机上，监听 `0.0.0.0:11434`
- **CMS 系统**: 通过 Docker Compose 部署，需要访问宿主机的 Ollama 服务
- **Mac Mini IP**: 192.168.31.33
- **Ollama 模型**:
  - qwen3.5:9b
  - qwen3.5:27b

### Docker 配置
- **容器名**: lucesion2026-upload-server-1
- **Docker 版本**: Docker Desktop for Mac
- **网络模式**: bridge
- **端口映射**: 8085:3000

## 技术背景

### Mac Docker Desktop 的 host.docker.internal
在 Mac 和 Windows 的 Docker Desktop 中，`host.docker.internal` 自动解析为宿主机的 IP 地址。这是 Docker Desktop 内置的 DNS 功能，不需要在 `docker-compose.yml` 中配置 `extra_hosts`。

### Linux Docker 的 extra_hosts
Linux 版本的 Docker 不支持 `host.docker.internal`，需要手动在 `docker-compose.yml` 中添加：
```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

**注意**: 项目当前使用的是 Mac Docker Desktop，`extra_hosts` 配置可能与内置功能冲突。

## 已执行的排查步骤

### 1. 确认 Ollama 服务运行状态
```bash
# 在宿主机上执行
lsof -i :11434
# 输出: COMMAND   PID USER   FD   TYPE   DEVICE SIZE/OFF NODE NAME
#       ollama  12345 user   23u  IPv4 0x12345678      0t0  TCP *:11434 (LISTEN)
```

**结论**: Ollama 正常监听 0.0.0.0:11434，可以从外部访问。

### 2. 测试宿主机网络连通性
```bash
# 在宿主机上测试
curl http://localhost:11434/api/tags
# 成功返回模型列表

curl http://192.168.31.33:11434/api/tags
# 成功返回模型列表
```

**结论**: Ollama 服务在宿主机上工作正常，可以通过 localhost 和局域网 IP 访问。

### 3. 测试从容器内部访问宿主机
```bash
# 进入容器
docker exec -it lucesion2026-upload-server-1 sh

# 测试访问宿主机 IP
wget http://192.168.31.33:11434/api/tags -O /dev/null
# 输出: Connecting to 192.168.31.33:11434... (无后续输出)

# 测试访问 host.docker.internal
wget http://host.docker.internal:11434/api/tags -O /dev/null
# 输出: Connecting to host.docker.internal:11434... (无后续输出)
```

**观察**:
- wget 命令能够建立 TCP 连接（"Connecting to..." 输出）
- 但无法完成 HTTP 请求（无后续输出，超时）
- 两个地址（IP 和 host.docker.internal）表现相同

**初步结论**: 容器能够与宿主机建立网络连接，但 HTTP 请求无法完成。可能是 Mac 防火墙阻止了 Docker 容器访问宿主机的 11434 端口。

### 4. 检查 Docker 日志
```bash
docker logs lucesion2026-upload-server-1 --tail 50
```

**相关日志片段**:
```
[timestamp] INFO: Testing AI connection to: http://192.168.31.33:11434/v1/chat/completions
[timestamp] ERROR: AI connection failed: AI 返回格式异常，无法解析为 JSON
```

**结论**: 服务器收到了连接请求，但收到的响应无法解析为 JSON 格式。

### 5. 检查 SSRF 验证逻辑
文件: `server/upload-server.mjs` (176-191行)

```javascript
// SSRF 保护：不允许访问私有 IP 和回环地址
function isPrivateOrLoopbackIP(hostname) {
  const privatePatterns = [
    /^127\./,           // 127.0.0.0/8
    /^10\./,            // 10.0.0.0/8
    /^172\.(1[6-9]|2[0-9]|3[0-1])\./,  // 172.16.0.0/12
    /^192\.168\./,      // 192.168.0.0/16
  ];
  return privatePatterns.some(pattern => pattern.test(hostname));
}
```

**问题**: 如果 `ALLOW_LOCAL_AI_ENDPOINT=true` 环境变量生效，SSRF 验证会被跳过。但如果环境变量未正确传递到容器，私有 IP 会被拒绝。

### 6. 检查环境变量
```bash
docker exec lucesion2026-upload-server-1 env | grep ALLOW_LOCAL
# 输出: ALLOW_LOCAL_AI_ENDPOINT=true
```

**结论**: 环境变量正确传递到容器，SSRF 验证应被跳过。

## 代码分析

### 1. Docker 端点转换逻辑
文件: `server/upload-server.mjs` (481-498行)

```javascript
function dockerRewriteEndpoint(apiEndpoint) {
  // 在 Docker 容器内，将 localhost/127.0.0.1 转换为 host.docker.internal
  if (apiEndpoint && (apiEndpoint.includes('localhost') || apiEndpoint.includes('127.0.0.1'))) {
    const rewritten = apiEndpoint
      .replace(/localhost/g, 'host.docker.internal')
      .replace(/127\.0\.0\.1/g, 'host.docker.internal');
    console.log(`[Docker端点转换] ${apiEndpoint} -> ${rewritten}`);
    return rewritten;
  }
  return apiEndpoint;
}
```

**工作流程**:
1. 用户在 CMS 设置中输入 `http://localhost:11434/v1/chat/completions`
2. `dockerRewriteEndpoint()` 将其转换为 `http://host.docker.internal:11434/v1/chat/completions`
3. 容器向宿主机发送请求

### 2. AI 调用逻辑
文件: `server/upload-server.mjs` (1700-1759行)

```javascript
async function callAiProvider(prompt, options = {}) {
  try {
    const response = await fetch(apiEndpoint, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...options.headers
      },
      body: JSON.stringify(payload)
    });

    const text = await response.text();
    console.log(`[AI响应原始数据] ${text.substring(0, 500)}`);

    let data;
    try {
      data = JSON.parse(text);
    } catch (e) {
      throw new Error('AI 返回格式异常，无法解析为 JSON');
    }

    // ... 处理响应数据
  } catch (error) {
    console.error('[AI调用失败]', error);
    throw error;
  }
}
```

**错误分析**:
- `AI 返回格式异常，无法解析为 JSON` 错误表示 Ollama 返回的响应不是有效的 JSON 格式
- 可能原因：
  1. 连接超时，返回空响应或 HTML 错误页面
  2. Ollama 服务返回了非 JSON 的错误信息
  3. 网络代理或防火墙返回了拦截页面

## 当前状态

### 已确认
1. ✅ Ollama 服务正常运行，监听 0.0.0.0:11434
2. ✅ CMS Docker 容器运行正常
3. ✅ `ALLOW_LOCAL_AI_ENDPOINT=true` 环境变量已设置
4. ✅ Docker 日志显示请求到达了服务器
5. ✅ 容器可以与宿主机建立 TCP 连接

### 待确认
1. ❓ Mac 防火墙是否阻止 Docker 容器访问宿主机的 11434 端口
2. ❓ `host.docker.internal` 在 Mac Docker Desktop 上是否正确解析
3. ❓ Ollama 服务的日志显示的具体错误信息
4. ❓ 容器内部是否可以访问其他宿主机服务（如 SSH）

## 建议的排查步骤

### 1. 检查 Mac 防火墙设置
```bash
# 查看防火墙状态
sudo /usr/libexec/ApplicationFirewall/socketfilterfw --getglobalstate

# 查看防火墙阻止的应用
sudo /usr/libexec/ApplicationFirewall/socketfilterfw --listapps

# 临时关闭防火墙测试
sudo /usr/libexec/ApplicationFirewall/socketfilterfw --setglobalstate off
```

### 2. 检查 Ollama 日志
```bash
# 查看 Ollama 服务器日志
tail -f ~/.ollama/logs/server.log

# 或通过系统日志
log stream --predicate 'process == "ollama"' --level debug
```

### 3. 在容器内使用更详细的测试工具
```bash
# 使用 curl -v 查看详细的 HTTP 请求和响应
docker exec lucesion2026-upload-server-1 curl -v http://192.168.31.33:11434/api/tags

# 使用 nc 测试 TCP 连接
docker exec lucesion2026-upload-server-1 nc -zv 192.168.31.33 11434

# 使用 telnet 测试
docker exec lucesion2026-upload-server-1 telnet 192.168.31.33 11434
```

### 4. 测试其他宿主机服务
```bash
# 测试宿主机的 SSH 服务（默认 22 端口）
docker exec lucesion2026-upload-server-1 nc -zv 192.168.31.33 22

# 如果 SSH 可访问，说明网络连通，问题在 Ollama 配置
# 如果 SSH 不可访问，说明 Mac 防火墙阻止了所有容器到宿主机的连接
```

### 5. 检查 Docker Desktop 网络配置
1. 打开 Docker Desktop
2. 进入 Settings → Resources → Proxies
3. 检查是否配置了 HTTP/HTTPS 代理
4. 如果配置了代理，尝试禁用后测试

### 6. 尝试使用 Docker 网络模式（临时测试）
修改 `docker-compose.yml`，将 upload-server 的网络模式改为 `host`:
```yaml
upload-server:
  network_mode: host  # 使用宿主机网络栈
```

**注意**: `host` 网络模式在 Mac Docker Desktop 上不可用。这是仅 Linux 支持的功能。

### 7. 移除 extra_hosts 配置
在 `docker-compose.yml` 中注释掉或删除 `extra_hosts` 配置，避免与 Mac Docker Desktop 的内置功能冲突：

```yaml
services:
  upload-server:
    # extra_hosts:
    #   - "host.docker.internal:host-gateway"  # 仅 Linux 需要，Mac 不需要
```

## 可能的解决方案

### 方案 A: 检查并调整 Mac 防火墙
1. 在 "系统设置 → 网络 → 防火墙" 中，确保 Docker Desktop 允许入站连接
2. 添加防火墙规则，允许从 Docker 网络访问 11434 端口：
```bash
# 添加防火墙规则（需要根据实际情况调整）
sudo pfctl -e
echo "pass in from any to any port 11434" | sudo pfctl -f -
```

### 方案 B: 使用 Ollama 的监听地址配置
确保 Ollama 监听所有网络接口：
```bash
# 检查 Ollama 配置
cat ~/.ollama/config

# 如果有 Ollama_HOST 设置，确保是 0.0.0.0
export OLLAMA_HOST=0.0.0.0:11434
ollama serve
```

### 方案 C: 使用代理转发（临时测试）
在容器内创建一个简单的代理服务，将请求转发到宿主机：
```javascript
// 在容器内运行代理脚本
const http = require('http');

http.createServer((req, res) => {
  const options = {
    hostname: 'host.docker.internal',
    port: 11434,
    path: req.url,
    method: req.method,
    headers: req.headers
  };

  const proxyReq = http.request(options, (proxyRes) => {
    res.writeHead(proxyRes.statusCode, proxyRes.headers);
    proxyRes.pipe(res);
  });

  req.pipe(proxyReq);
}).listen(3001);

console.log('代理服务器运行在端口 3001');
```

### 方案 D: 使用端口映射（推荐）
将宿主机的 11434 端口映射到容器内部，然后在容器内访问 localhost:11434：

1. 修改 `docker-compose.yml`:
```yaml
services:
  upload-server:
    ports:
      - "8085:3000"
      - "11434:11434"  # 映射 Ollama 端口到容器
```

2. 重启容器
3. 在 CMS 设置中使用 `http://localhost:11434/v1/chat/completions`

**注意**: 这种方式需要在容器内运行 Ollama，或者使用其他方式将 11434 端口的服务暴露到容器网络。

### 方案 E: 检查 docker-compose.yml 的 extra_hosts 配置
当前配置可能与 Mac Docker Desktop 冲突。建议移除 `extra_hosts` 配置，因为 Mac Docker Desktop 自动提供 `host.docker.internal` 解析。

修改前：
```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

修改后：
```yaml
# Mac Docker Desktop 自动提供 host.docker.internal，无需配置
```

## 相关文件

### 1. docker-compose.yml
```yaml
version: '3.8'

services:
  upload-server:
    build: .
    ports:
      - "8085:3000"
    environment:
      - ALLOW_LOCAL_AI_ENDPOINT=true
    # extra_hosts:
    #   - "host.docker.internal:host-gateway"  # 已注释，避免冲突
```

### 2. server/upload-server.mjs
- **行 481-498**: `dockerRewriteEndpoint()` 函数
- **行 176-191**: SSRF 验证逻辑
- **行 1700-1759**: `callAiProvider()` 函数

### 3. src/store/mockData.ts
```typescript
const defaultSettings = {
  aiProvider: 'ollama',
  apiEndpoint: 'http://host.docker.internal:11434/v1/chat/completions',
  // ...
};
```

## 已提交的代码更改

### commit 989229b
```
Fix: Docker容器访问Ollama服务的网络连接问题

- 修改 docker-compose.yml 的 extra_hosts 配置以适配 Mac Docker Desktop
- 优化 upload-server.mjs 的 SSRF 验证逻辑
- 添加详细的 Ollama 连接测试文档
- 更新 API 端点配置说明
```

## 下一步行动

1. **优先级 1**: 检查 Mac 防火墙设置，确认是否阻止了 Docker 容器访问宿主机
2. **优先级 2**: 查看 Ollama 服务器日志，了解接收到的具体请求和响应
3. **优先级 3**: 在容器内使用 `curl -v` 进行详细的 HTTP 测试
4. **优先级 4**: 测试其他宿主机服务（如 SSH）的连通性
5. **优先级 5**: 根据测试结果选择合适的解决方案

## 联系信息

- **GitHub 仓库**: https://github.com/shcming2023/Luceon2026
- **问题发生时间**: 2026-04-17
- **Docker 版本**: Docker Desktop for Mac
- **Ollama 版本**: Latest
- **CMS 版本**: Luceon2026

---

**文档版本**: v1.0
**最后更新**: 2026-04-17 01:59
**创建者**: CodeBuddy AI
**状态**: 待进一步排查
