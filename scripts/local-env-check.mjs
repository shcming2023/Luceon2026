import { execSync } from 'child_process';
import http from 'http';
import net from 'net';

const ports = {
  CMS: 8080,
  UploadServer: 8788,
  DbServer: 8789,
  MinerULocal: 8083,
  Ollama: 11434,
  MinioConsole: 19001,
  MinioAPI: 9000
};

console.log('=== Tier 2 Local UAT Baseline Check ===\n');

// 1. Check Node/npm availability
try {
  const nodeVer = execSync('node -v', { encoding: 'utf-8' }).trim();
  const npmVer = execSync('npm -v', { encoding: 'utf-8' }).trim();
  console.log(`[OK] Node.js is available: ${nodeVer}`);
  console.log(`[OK] npm is available: v${npmVer}`);
} catch (e) {
  console.error('[FAIL] Node or npm is not correctly installed or in PATH.');
  process.exit(1);
}

// 2. Check Docker daemon
try {
  execSync('docker info', { stdio: 'ignore' });
  console.log('[OK] Docker daemon is running.');
} catch (e) {
  console.error('[FAIL] Docker daemon is NOT running or not accessible. Please start Docker Desktop.');
  process.exit(1);
}

// 3. Check Compose config
try {
  execSync('docker compose -f docker-compose.yml -f docker-compose.local.yml -f docker-compose.override.yml config --quiet', { stdio: 'ignore' });
  console.log('[OK] Docker Compose configuration is valid.');
} catch (e) {
  console.error('[FAIL] Docker Compose configuration validation failed.');
  process.exit(1);
}

// 4. Check port availability (before starting)
// Note: This just attempts to listen on the port briefly to see if it's free.
// If Docker is already running, it might fail. We assume we check this before `up`.
const checkPort = (name, port) => {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.once('error', (err) => {
      if (err.code === 'EADDRINUSE') {
        resolve({ name, port, status: 'IN_USE' });
      } else {
        resolve({ name, port, status: 'ERROR' });
      }
    });
    server.once('listening', () => {
      server.close();
      resolve({ name, port, status: 'FREE' });
    });
    server.listen(port);
  });
};

async function runChecks() {
  console.log('\nChecking Ports Availability:');
  const results = await Promise.all([
    checkPort('CMS', ports.CMS),
    checkPort('MinerU Host', ports.MinerULocal),
    checkPort('Ollama', ports.Ollama),
    checkPort('Minio Console', ports.MinioConsole)
  ]);

  let portConflict = false;
  results.forEach(r => {
    if (r.status === 'IN_USE') {
      console.warn(`[WARN] Port ${r.port} (${r.name}) is ALREADY IN USE. If containers are not running, this will cause a conflict!`);
      portConflict = true;
    } else if (r.status === 'FREE') {
      console.log(`[OK] Port ${r.port} (${r.name}) is free.`);
    }
  });

  console.log(`\n=== Current UAT URL: http://localhost:${ports.CMS}/cms/ ===`);
  console.log('\nTo start the environment on Windows, run:');
  console.log('docker compose -f docker-compose.yml -f docker-compose.local.yml -f docker-compose.override.yml up -d --build');
  console.log('\nFor a lightweight mock environment, add the tier2-lite override:');
  console.log('docker compose -f docker-compose.yml -f docker-compose.local.yml -f docker-compose.tier2-lite.yml up -d --build');

  if (portConflict) {
    console.log('\n⚠️ WARNING: Some ports are already in use. Ensure no other instances are running.');
  }
}

runChecks();
