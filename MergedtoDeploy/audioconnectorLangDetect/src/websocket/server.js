import WS, { WebSocket } from 'ws';
import express from 'express';
import http from 'http';
import { Session } from '../common/session.js';
import { getPort } from '../common/environment-variables.js';
import { SecretService } from '../services/secret-service.js';
import { WebSocketServer } from 'ws';
import { v4 as uuid } from 'uuid';

export class Server {

    constructor() {

        this.app = undefined;

        this.httpServer = undefined;

        this.wsServer = undefined;

        this.sessionMap = new Map();

        this.secretService =
            new SecretService();

        this.timeoutDuration =
            process.env.timeoutDuration;

        // MONITOR CLIENTS
        this.monitorClients = [];

        // CONVERSATION STATE
        this.conversationState =
            new Map();
    }

    // =========================
    // HELPERS
    // =========================

    isInternalClient(request) {

        return (
            request.headers[
                "x-internal-client"
            ] === "monitor"
        );
    }

    broadcastToMonitorClients(
        payload
    ) {

        this.monitorClients =
            this.monitorClients.filter(
                client =>
                    client.readyState ===
                    WS.OPEN
            );

        this.monitorClients.forEach(
            client => {

                try {

                    client.send(
                        JSON.stringify(
                            payload
                        )
                    );

                } catch (err) {

                    console.error(
                        "Monitor broadcast failed:",
                        err
                    );
                }
            }
        );
    }

    // =========================
    // START SERVER
    // =========================

    start(externalServer) {

        if (!externalServer) {

            this.app = express();

            this.httpServer =
                http.createServer(
                    this.app
                );

            console.log(
                `Created internal HTTP server, but NOT listening`
            );

        } else {

            this.httpServer =
                externalServer;

            console.log(
                `Using external HTTP server on port ${getPort()}`
            );
        }

        this.wsServer =
            new WebSocketServer({
                server:
                    this.httpServer
            });

        this.wsServer.on(
            'connection',

            (ws, request) => {

                console.log(
                    'New WebSocket connection.'
                );

                // =========================
                // INTERNAL MONITOR CLIENT
                // =========================

                if (
                    this.isInternalClient(
                        request
                    )
                ) {

                    console.log(
                        'Monitor client connected.'
                    );

                    this.monitorClients.push(
                        ws
                    );

                    ws.on(
                        'close',
                        () => {

                            console.log(
                                'Monitor client disconnected.'
                            );

                            this.monitorClients =
                                this.monitorClients.filter(
                                    client =>
                                        client !== ws
                                );
                        }
                    );

                    ws.on(
                        'error',
                        err => {

                            console.error(
                                'Monitor client error:',
                                err
                            );
                        }
                    );

                    // IMPORTANT:
                    // DO NOT CREATE SESSION
                    return;
                }

                // =========================
                // GENESYS SESSION
                // =========================

                ws.on(
                    'close',

                    () => {

                        console.log(
                            'WebSocket connection closed.'
                        );

                        this.deleteConnection(
                            ws
                        );
                    }
                );

                ws.on(
                    'error',

                    error => {

                        console.log(
                            `WebSocket Error: ${error}`
                        );

                        ws.close();
                    }
                );

                ws.on(
                    'message',

                    async (
                        data,
                        isBinary
                    ) => {

                        if (
                            ws.readyState !==
                            WS.OPEN
                        ) {

                            return;
                        }

                        const session =
                            this.sessionMap.get(
                                ws
                            );

                        // =========================
                        // SESSION NOT FOUND
                        // =========================

                        if (
                            !session
                        ) {

                            const dummySession =
                                new Session(
                                    ws,
                                    request.headers[
                                        'audiohook-session-id'
                                    ],
                                    request.url
                                );

                            console.log(
                                'Session does not exist.'
                            );

                            dummySession.sendDisconnect(
                                'error',
                                'Session does not exist.',
                                {}
                            );

                            return;
                        }

                        // =========================
                        // TEXT MESSAGE
                        // =========================

                        if (
                            !isBinary
                        ) {

                            let message;

                            try {

                                message =
                                    JSON.parse(
                                        data.toString()
                                    );

                            } catch (err) {

                                console.error(
                                    'Failed to parse text message:',
                                    err
                                );

                                return;
                            }

                            // =========================
                            // MONITOR EVENT
                            // =========================

                            if (
                                message.type ===
                                'monitor'
                            ) {

                                console.log(
                                    'Incoming monitor event.'
                                );

                                const audioBuffer =
                                    Buffer.from(
                                        message.binary,
                                        'base64'
                                    );

                                console.log(
                                    'Monitor audio bytes:',
                                    audioBuffer.length
                                );

                                const convId =
                                    message.conversationId ||
                                    uuid();

                                let convState =
                                    this.conversationState.get(
                                        convId
                                    );

                                if (
                                    !convState
                                ) {

                                    convState = {};

                                    this.conversationState.set(
                                        convId,
                                        convState
                                    );
                                }

                                convState.lastMonitorAudio =
                                    audioBuffer;

                                // =========================
                                // BROADCAST TO MONITORS
                                // =========================

                                this.broadcastToMonitorClients(
                                    {
                                        type:
                                            'monitor-event',

                                        conversationId:
                                            convId,

                                        audioBytes:
                                            audioBuffer.length,

                                        timestamp:
                                            new Date()
                                                .toISOString()
                                    }
                                );

                                return;
                            }

                            // =========================
                            // NORMAL TEXT MESSAGE
                            // =========================

                            session.processTextMessage(
                                data.toString()
                            );

                            return;
                        }

                        // =========================
                        // BINARY AUDIO
                        // =========================

                       // console.log(
                            //'Binary audio received:',
                           // data.length
                      //  );

                        session.handleBinaryMessage(
                            data
                        );
                    }
                );

                this.createConnection(
                    ws,
                    request
                );

                // OPTIONAL TIMEOUT
                // this.setConnectionTimeout(ws);
            }
        );
    }

    // =========================
    // CREATE SESSION
    // =========================

    createConnection(
        ws,
        request
    ) {

        let session =
            this.sessionMap.get(
                ws
            );

        if (session) {

            return;
        }

        session = new Session(
            ws,
            request.headers[
                'audiohook-session-id'
            ],
            request.url
        );

        console.log(
            'Creating a new session.'
        );

        this.sessionMap.set(
            ws,
            session
        );
    }

    // =========================
    // DELETE SESSION
    // =========================

    deleteConnection(ws) {

        const session =
            this.sessionMap.get(
                ws
            );

        if (!session) {

            return;
        }

        try {

            session.close();

        } catch { }

        console.log(
            'Deleting session.'
        );

        this.sessionMap.delete(
            ws
        );
    }

    // =========================
    // OPTIONAL TIMEOUT
    // =========================

    /*
    setConnectionTimeout(ws) {

        ws._timeout = setTimeout(() => {

            if (
                ws.readyState === WS.OPEN
            ) {

                console.log(
                    'Connection timed out.'
                );

                ws.close();
            }

        }, this.timeoutDuration);
    }
    */
}