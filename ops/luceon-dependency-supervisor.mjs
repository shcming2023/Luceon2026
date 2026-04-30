import express from 'express';
import { exec } from 'child_process';
import util from 'util';

const execPromise = process.env.MOCK_EXEC === 'true' 
  ? async (cmd) => { console.log(`[MOCK_EXEC] ${cmd}`); if (cmd.includes('has-session') && process.env.MOCK_TMUX_HAS_SESSION === 'true') return { stdout: '' }; if (cmd.includes('has-session')) throw new Error('mock no session'); return { stdout: 'mocked' }; }
  : util.promisify(exec);
const app = express();
app.use(express.json());

const PORT = 18088; // Supervisor port

const ALLOWED_ACTIONS = {
  'start-mineru': true,
  'restart-mineru': true,
  'start-sidecar': true,
  'restart-sidecar': true,
};

async function checkSession(name) {
  try {
    await execPromise(`tmux has-session -t ${name}`);
    return true;
  } catch {
    return false;
  }
}

// 状态检查
app.get('/status', async (req, res) => {
  try {
    const mineru = await checkSession('luceon-mineru');
    const sidecar = await checkSession('luceon-sidecar');
    res.json({ ok: true, message: 'Supervisor running', sessions: { mineru, sidecar } });
  } catch (error) {
    res.json({ ok: true, message: 'Supervisor running', sessions: { mineru: false, sidecar: false } });
  }
});

// 执行动作
app.post('/action', async (req, res) => {
  const { action } = req.body;
  if (!action || !ALLOWED_ACTIONS[action]) {
    return res.status(400).json({ ok: false, error: 'Invalid or missing action' });
  }

  try {
    console.log(`[Supervisor] Executing action: ${action}`);
    
    // ops目录在repo根目录下，且该脚本也是从repo根目录启动的
    const repoRoot = process.cwd();

    if (action === 'start-mineru') {
      if (await checkSession('luceon-mineru')) {
        return res.json({ ok: true, action, detached: true, session: 'luceon-mineru', message: 'MinerU API already running in tmux session' });
      }
      await execPromise(`tmux new-session -d -s luceon-mineru "cd '${repoRoot}' && bash ops/start-mineru-api.sh"`);
      return res.json({ ok: true, action, detached: true, session: 'luceon-mineru', message: 'MinerU API start requested in tmux session' });
    }

    if (action === 'restart-mineru') {
      await execPromise(`tmux kill-session -t luceon-mineru 2>/dev/null || true`);
      await execPromise(`tmux new-session -d -s luceon-mineru "cd '${repoRoot}' && bash ops/start-mineru-api.sh"`);
      return res.json({ ok: true, action, detached: true, session: 'luceon-mineru', message: 'MinerU API restart requested in tmux session' });
    }

    if (action === 'start-sidecar') {
      if (await checkSession('luceon-sidecar')) {
        return res.json({ ok: true, action, detached: true, session: 'luceon-sidecar', message: 'Sidecar already running in tmux session' });
      }
      await execPromise(`tmux new-session -d -s luceon-sidecar "cd '${repoRoot}' && UPLOAD_SERVER_URL=http://127.0.0.1:8081/__proxy/upload node ops/mineru-log-observer.mjs"`);
      return res.json({ ok: true, action, detached: true, session: 'luceon-sidecar', message: 'Sidecar start requested in tmux session' });
    }

    if (action === 'restart-sidecar') {
      await execPromise(`tmux kill-session -t luceon-sidecar 2>/dev/null || true`);
      await execPromise(`tmux new-session -d -s luceon-sidecar "cd '${repoRoot}' && UPLOAD_SERVER_URL=http://127.0.0.1:8081/__proxy/upload node ops/mineru-log-observer.mjs"`);
      return res.json({ ok: true, action, detached: true, session: 'luceon-sidecar', message: 'Sidecar restart requested in tmux session' });
    }
    
  } catch (error) {
    console.error(`[Supervisor] Action ${action} failed:`, error.message);
    res.status(500).json({ ok: false, action, error: error.message });
  }
});

app.listen(PORT, '127.0.0.1', () => {
  console.log(`[Luceon Supervisor] Listening on http://127.0.0.1:${PORT}`);
  console.log(`[Luceon Supervisor] Allowed actions: ${Object.keys(ALLOWED_ACTIONS).join(', ')}`);
});
