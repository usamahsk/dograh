import dotenv from 'dotenv';
import { Server } from './websocket/server.js';
import express from 'express';
import http from 'http';
// Note: The line `import { http } from '@google-cloud/functions-framework';`
// is commented out because it conflicts with the Node.js http import and
// `@google-cloud/functions-framework` does not export `http` like that.

console.log('Starting service.');
dotenv.config();

const wsServer = new Server();
const expressApp = express();
const httpServer = http.createServer(expressApp);
wsServer.initializeWebSocket(httpServer);

wsServer.start();

export { expressApp, httpServer, wsServer };


