import { Client } from 'minio';
const client = new Client({
  endPoint: '127.0.0.1',
  port: 9000,
  useSSL: false,
  accessKey: 'admin',
  secretKey: 'admin123'
});
async function run() {
  try {
    const url = await client.presignedUrl('GET', 'bucket', '', 3600, { 'list-type': '2', prefix: 'test/' });
    console.log(url);
  } catch (e) {
    console.error(e);
  }
}
run();
