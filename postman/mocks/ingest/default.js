// Contract subset from device/ingest.py, plus a diagnostic GET /received.
// Fail the first six segment attempts to exhaust one pusher retry cycle.
const http = require('http');
http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  const received = await pm.state.get('ingest.received') || [];
  if (req.method === 'POST' && url.pathname === '/reset') {
    await pm.state.set('ingest.attempts', 0);
    await pm.state.set('ingest.received', []);
    res.end('ok');
  } else if (req.method === 'POST' && url.pathname === '/seg') {
    const attempts = (await pm.state.get('ingest.attempts') || 0) + 1;
    await pm.state.set('ingest.attempts', attempts);
    if (attempts <= 6) {
      res.writeHead(503).end('temporarily unavailable');
      return;
    }
    const sequence = Number(url.searchParams.get('n'));
    if (!received.includes(sequence)) received.push(sequence);
    await pm.state.set('ingest.received', received);
    res.end('ok');
  } else if (req.method === 'POST' && url.pathname === '/pl') {
    res.end('ok');
  } else if (req.method === 'GET' && url.pathname === '/received') {
    res.setHeader('Content-Type', 'application/json');
    res.end(JSON.stringify({ received, attempts: await pm.state.get('ingest.attempts') || 0 }));
  } else {
    res.writeHead(501).end('not modeled');
  }
}).listen(process.env.PORT);
