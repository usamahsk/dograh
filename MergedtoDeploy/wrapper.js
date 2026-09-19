import * as http from 'http';
import express from 'express';

import translatorApp from './LinuxDigitalTranslator/index.js';
import { Server as WebSocketServer } from './audioconnectorLangDetect/src/websocket/server.js';

const mainApp = express();
mainApp.use('/translate-service', translatorApp);

const PORT = process.env.PORT; // no fallback, Azure provides this port

const httpServer = http.createServer(mainApp);

// Pass shared HTTP server to WebSocket Server
const wsServer = new WebSocketServer();
wsServer.start(httpServer);

httpServer.listen(PORT, () => {
    console.log(`Unified HTTP/WebSocket server listening on port ${PORT}`);
});
