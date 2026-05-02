import { execSync } from 'child_process';
import net from 'net';

async function checkPort(port, host = '127.0.0.1') {
  return new Promise((resolve) => {
    const socket = new net.Socket();
    socket.setTimeout(1000);
    socket.on('connect', () => {
      socket.destroy();
      resolve(true); // In use
    });
    socket.on('timeout', () => {
      socket.destroy();
      resolve(false); // Free
    });
    socket.on('error', () => {
      socket.destroy();
      resolve(false); // Free
    });
    socket.connect(port, host);
  });
}

function runCmd(cmd) {
  try {
    const stdout = execSync(cmd, { stdio: 'pipe', encoding: 'utf-8' });
    return stdout.trim();
  } catch (err) {
    return null;
  }
}

async function main() {
  console.log('=== Luceon2026 Local Tier 2 UAT Pre-check ===');

  // Node version
  const nodeVer = runCmd('node -v');
  console.log(`- Node version: ${nodeVer || 'Not found'}`);

  // npm / npx
  const isWindows = process.platform === 'win32';
  const npmCmd = isWindows ? 'npm.cmd' : 'npm';
  const npxCmd = isWindows ? 'npx.cmd' : 'npx';

  const npmVer = runCmd(`${npmCmd} -v`);
  console.log(`- npm version: ${npmVer || 'Not found'} (using ${npmCmd})`);

  const npxVer = runCmd(`${npxCmd} -v`);
  console.log(`- npx version: ${npxVer || 'Not found'} (using ${npxCmd})`);

  if (!npmVer || !npxVer) {
    console.error(`❌ ${npmCmd} or ${npxCmd} not available. Please ensure Node.js is installed properly.`);
    process.exit(1);
  }

  // Docker daemon
  const dockerVer = runCmd('docker info --format "{{.ServerVersion}}"');
  if (dockerVer) {
    console.log(`- Docker daemon: Running (v${dockerVer})`);
  } else {
    console.error('❌ Docker daemon is not running or not accessible.');
    process.exit(1);
  }

  // Docker Compose
  const composeVer = runCmd('docker compose version');
  console.log(`- Docker Compose: ${composeVer || 'Not found'}`);

  // Compose config check
  const composeConfig = runCmd('docker compose -f docker-compose.yml -f docker-compose.local.yml -f docker-compose.override.yml config --quiet');
  if (composeConfig !== null) {
    console.log(`- Compose config: Passed`);
  } else {
    console.error('❌ Compose config check failed.');
    process.exit(1);
  }

  // Port checks
  const portsToCheck = [
    { port: 8080, name: 'CMS Frontend (8080)' },
    { port: 8083, name: 'MinerU Local (8083)' },
    { port: 11434, name: 'Ollama (11434)' },
    { port: 19001, name: 'MinIO Console (19001)' }
  ];

  console.log('\n--- Port Status ---');
  for (const { port, name } of portsToCheck) {
    const inUse = await checkPort(port);
    if (inUse) {
      console.log(`⚠️  ${name} is currently IN USE (could be conflicting or already running).`);
    } else {
      console.log(`✅ ${name} is FREE.`);
    }
  }

  console.log('\n--- Recommended Access URLs ---');
  console.log('- CMS Local URL:      http://127.0.0.1:8080/cms/');
  console.log('- MinIO Console URL:  http://127.0.0.1:19001');
  console.log('- MinerU URL:         http://127.0.0.1:8083');
  console.log('- Ollama URL:         http://127.0.0.1:11434');
  console.log('\n✅ Pre-check completed.');
}

main().catch(console.error);
