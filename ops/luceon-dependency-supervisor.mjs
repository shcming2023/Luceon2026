import express from 'express';
import { exec } from 'child_process';
import util from 'util';

const execPromise = util.promisify(exec);
const app = express();
app.use(express.json());

const PORT = 18088; // Supervisor port

const ALLOWED_ACTIONS = {
  'start-mineru': 'bash ops/start-mineru-api.sh',
  'restart-mineru': 'bash ops/start-mineru-api.sh', // Since tmux start script might recreate or restart
  'start-sidecar': 'tmux kill-session -t luceon-sidecar 2>/dev/null || true; tmux new-session -d -s luceon-sidecar "UPLOAD_SERVER_URL=http://127.0.0.1:8081/__proxy/upload node ops/mineru-log-observer.mjs"',
  'restart-sidecar': 'tmux kill-session -t luceon-sidecar 2>/dev/null || true; tmux new-session -d -s luceon-sidecar "UPLOAD_SERVER_URL=http://127.0.0.1:8081/__proxy/upload node ops/mineru-log-observer.mjs"',
};

// 状态检查
app.get('/status', (req, res) => {
  res.json({ ok: true, message: 'Supervisor running' });
});

// 执行动作
app.post('/action', async (req, res) => {
  const { action } = req.body;
  if (!action || !ALLOWED_ACTIONS[action]) {
    return res.status(400).json({ ok: false, error: 'Invalid or missing action' });
  }

  try {
    const cmd = ALLOWED_ACTIONS[action];
    console.log(`[Supervisor] Executing action: ${action} -> ${cmd}`);
    
    // We run it detached or just exec, but for tmux commands exec is fine
    // as tmux spawns a detached session anyway.
    const { stdout, stderr } = await execPromise(cmd, { timeout: 10000 });
    
    res.json({ ok: true, action, stdout, stderr });
  } catch (error) {
    console.error(`[Supervisor] Action ${action} failed:`, error.message);
    res.status(500).json({ ok: false, action, error: error.message });
  }
});

app.listen(PORT, '127.0.0.1', () => {
  console.log(`[Luceon Supervisor] Listening on http://127.0.0.1:${PORT}`);
  console.log(`[Luceon Supervisor] Allowed actions: ${Object.keys(ALLOWED_ACTIONS).join(', ')}`);
});
