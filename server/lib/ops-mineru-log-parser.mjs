import fs from 'fs';
import path from 'path';

export function parseTqdmLine(line) {
  // 匹配类似：Predict: 52%|█████▏    | 14/27 [02:04<01:52,  8.66s/it]
  // 匹配类似：Processing pages: 78%|███████▊  | 21/27 [...]
  const match = line.match(/([a-zA-Z0-9\s_]+?):\s*(\d+)%\|.*?\|\s*(\d+)\/(\d+)/);
  if (!match) return null;

  const phase = match[1].trim();
  const percent = parseInt(match[2], 10);
  const current = parseInt(match[3], 10);
  const total = parseInt(match[4], 10);

  return {
    source: 'mineru-log',
    phase,
    percent,
    current,
    total,
    rawLine: line.trim()
  };
}

export async function readTail(filePath, bytes = 4096) {
  return new Promise((resolve) => {
    fs.stat(filePath, (err, stats) => {
      if (err || !stats.isFile()) {
        resolve('');
        return;
      }
      const size = stats.size;
      const readSize = Math.min(size, bytes);
      const position = size - readSize;

      fs.open(filePath, 'r', (err, fd) => {
        if (err) {
          resolve('');
          return;
        }
        const buffer = Buffer.alloc(readSize);
        fs.read(fd, buffer, 0, readSize, position, (err, bytesRead, buffer) => {
          fs.close(fd, () => {
            if (err) resolve('');
            else resolve(buffer.toString('utf-8'));
          });
        });
      });
    });
  });
}

export async function parseLatestMineruProgress(minObservedAt) {
  const logPaths = [
    process.env.MINERU_ERR_LOG_PATH || '/Users/concm/ops/logs/mineru-api.err.log',
    process.env.MINERU_LOG_PATH || '/Users/concm/ops/logs/mineru-api.log',
    // Fallback locally for dev
    path.join(process.cwd(), 'uat', 'scratch', 'mineru-api.err.log'),
    path.join(process.cwd(), 'uat', 'scratch', 'mineru-api.log')
  ];

  let bestProgress = null;
  // File modification times to see which log is newer
  let latestMtime = 0;

  for (const filePath of logPaths) {
    try {
      if (!fs.existsSync(filePath)) continue;
      const stats = fs.statSync(filePath);
      const content = await readTail(filePath, 8192); // Read last 8KB
      if (!content) continue;

      // Split by \r or \n since tqdm often uses \r to overwrite lines
      const lines = content.split(/[\r\n]+/);
      let localBest = null;
      for (const line of lines) {
        const parsed = parseTqdmLine(line);
        if (parsed) {
          localBest = parsed;
        }
      }

      if (localBest) {
        if (stats.mtimeMs > latestMtime) {
          latestMtime = stats.mtimeMs;
          bestProgress = {
            ...localBest,
            observedAt: new Date(stats.mtimeMs).toISOString()
          };
        }
      }
    } catch (e) {
      // Ignore read errors
    }
  }

  if (bestProgress && minObservedAt) {
    const logTime = new Date(bestProgress.observedAt).getTime();
    const minTime = new Date(minObservedAt).getTime();
    if (logTime < minTime) {
      // Log is older than the current task's start time -> it's a stale log from a previous task
      return null;
    }
  }

  return bestProgress;
}
