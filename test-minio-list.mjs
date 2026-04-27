import { Client } from 'minio';

const minioClient = new Client({
  endPoint: '127.0.0.1',
  port: 9000,
  useSSL: false,
  accessKey: 'admin',
  secretKey: 'admin123'
});

async function run() {
  const url = await minioClient.presignedUrl('GET', 'luceon-parsed', '', 3600, { prefix: 'parsed/2994194655610866/', 'list-type': '2', 'max-keys': '500' });
  console.log('Presigned URL:', url);
  const res = await fetch(url);
  const text = await res.text();
  console.log(text.substring(0, 300));
}
run();
