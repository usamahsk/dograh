// audiohookMonitor.js

import express from "express";
import http from "http";
import { WebSocketServer } from "ws";

import dotenv from "dotenv";

import { Session } from "./audioconnectorLangDetect/src/common/sessionDeepGramMonitor.js";

dotenv.config();

// ---------------------------------------------------
// Configuration
// ---------------------------------------------------

const PORT =
    process.env.PORT || 8081;

// ---------------------------------------------------
// Express Setup
// ---------------------------------------------------

const app = express();

const httpServer =
    http.createServer(app);

const wsServer =
    new WebSocketServer({
        server: httpServer
    });

// ---------------------------------------------------
// Active Sessions
// ---------------------------------------------------

const sessionMap = new Map();

// ---------------------------------------------------
// WebSocket Server
// ---------------------------------------------------

wsServer.on(
    "connection",
    async (ws, request) => {

        console.log(
            "\n========================================"
        );

        console.log(
            "NEW AUDIOHOOK CONNECTION"
        );

        console.log(
            "========================================"
        );

        console.log(
            `URL: ${request.url}`
        );

        console.log(
            "\nHEADERS:\n"
        );

        console.log(
            JSON.stringify(
                request.headers,
                null,
                2
            )
        );

        // ---------------------------------------------------
        // Session ID
        // ---------------------------------------------------

        const sessionId =
            request.headers[
                "audiohook-session-id"
            ] ||
            `session-${Date.now()}`;

        console.log(
            `\nAssigned Session ID: ${sessionId}`
        );

        // ---------------------------------------------------
        // Create Session
        // ---------------------------------------------------

        const session =
            new Session(
                ws,
                sessionId,
                request.url
            );

        sessionMap.set(
            ws,
            session
        );

        // ---------------------------------------------------
        // Incoming Messages
        // ---------------------------------------------------

        ws.on(
            "message",
            async (
                data,
                isBinary
            ) => {

                if (
                    ws.readyState !== ws.OPEN
                ) {
                    return;
                }

                try {

                    // ------------------------------------------------
                    // Binary Audio
                    // ------------------------------------------------

                    if (isBinary) {

                        //console.log(
                       //     `[${sessionId}] AUDIO CHUNK | ${data.length} bytes`
                      //  );

                        await session.handleBinaryMessage(
                            data
                        );

                        return;
                    }

                    // ------------------------------------------------
                    // Text / JSON Events
                    // ------------------------------------------------

                    const msg =
                        data.toString();

                   // console.log(
                   //     `\n[${sessionId}] TEXT MESSAGE`
                   // );

                    //console.log(
                   //     msg
                   // );

                    let parsed;

                    try {

                        parsed =
                            JSON.parse(
                                msg
                            );

                    } catch (err) {

                        console.log(
                            `[${sessionId}] Non-JSON Text Message`
                        );

                        session.processTextMessage(
                            msg
                        );

                        return;
                    }

                    // ------------------------------------------------
                    // AudioHook Events
                    // ------------------------------------------------

                    switch (
                        parsed.type
                    ) {

                        // --------------------------------------------
                        // OPEN
                        // --------------------------------------------

                        case "open":

                            console.log(
                                "\n========================================"
                            );

                            console.log(
                                `[${sessionId}] AUDIOHOOK SESSION OPEN`
                            );

                            console.log(
                                "========================================"
                            );

                            console.log(
                                `Conversation ID : ${
                                    parsed.parameters
                                        ?.conversationId ||
                                    "N/A"
                                }`
                            );

                            console.log(
                                `ANI : ${
                                    parsed.parameters
                                        ?.participant
                                        ?.ani ||
                                    "N/A"
                                }`
                            );

                            console.log(
                                `DNIS : ${
                                    parsed.parameters
                                        ?.participant
                                        ?.dnis ||
                                    "N/A"
                                }`
                            );

                            console.log(
                                `Language : ${
                                    parsed.parameters
                                        ?.language ||
                                    "N/A"
                                }`
                            );

                            console.log(
                                `Media : ${
                                    JSON.stringify(
                                        parsed.parameters
                                            ?.media
                                    ) ||
                                    "N/A"
                                }`
                            );

                            console.log(
                                "========================================\n"
                            );

                            // ----------------------------------------
                            // Initialize Deepgram RT Client
                            // ----------------------------------------

                            try {

                               // await session.initializeRealTimeClient();

                                console.log(
                                    `[${sessionId}] Deepgram RT Client Initialized`
                                );

                            } catch (err) {

                                console.error(
                                    `[${sessionId}] Failed initializing RT client`,
                                    err
                                );
                            }

                            break;

                        // --------------------------------------------
                        // PAUSED
                        // --------------------------------------------

                        case "paused":

                            console.log(
                                `[${sessionId}] Audio Paused`
                            );

                            break;

                        // --------------------------------------------
                        // RESUMED
                        // --------------------------------------------

                        case "resumed":

                            console.log(
                                `[${sessionId}] Audio Resumed`
                            );

                            break;

                        // --------------------------------------------
                        // PING
                        // --------------------------------------------

                        case "ping":

                           // console.log(
                           //     `[${sessionId}] Ping Received`
                           // );

                            break;

                        // --------------------------------------------
                        // CLOSE
                        // --------------------------------------------

                        case "close":

                            console.log(
                                `[${sessionId}] Session Closing`
                            );

                            break;

                        // --------------------------------------------
                        // ERROR
                        // --------------------------------------------

                        case "error":

                            console.error(
                                `[${sessionId}] Error Event`,
                                parsed
                            );

                            break;

                        // --------------------------------------------
                        // DEFAULT
                        // --------------------------------------------

                        default:

                            console.log(
                                `[${sessionId}] Event Type: ${
                                    parsed.type ||
                                    "UNKNOWN"
                                }`
                            );
                    }

                    // ------------------------------------------------
                    // Existing Session Processing
                    // ------------------------------------------------

                    session.processTextMessage(
                        msg
                    );

                } catch (err) {

                    console.error(
                        `[${sessionId}] Message Processing Error`,
                        err
                    );

                }

            }
        );

        // ---------------------------------------------------
        // Close
        // ---------------------------------------------------

        ws.on(
            "close",
            async () => {

                console.log(
                    `\n[${sessionId}] WebSocket Closed`
                );

                try {

                    await session.close();

                } catch (err) {

                    console.error(
                        `[${sessionId}] Session Close Error`,
                        err
                    );
                }

                deleteConnection(
                    ws
                );

            }
        );

        // ---------------------------------------------------
        // Error
        // ---------------------------------------------------

        ws.on(
            "error",
            (error) => {

                console.error(
                    `\n[${sessionId}] WebSocket Error`,
                    error
                );

                ws.close();

            }
        );

    }
);

// ---------------------------------------------------
// Cleanup
// ---------------------------------------------------

function deleteConnection(ws) {

    const session =
        sessionMap.get(ws);

    if (!session) {
        return;
    }

    sessionMap.delete(ws);

    console.log(
        "Session Deleted"
    );
}

// ---------------------------------------------------
// Health Endpoint
// ---------------------------------------------------

app.get(
    "/",
    (req, res) => {

        res.send({

            status:
                "AudioHook Monitor Running",

            activeSessions:
                sessionMap.size
        });

    }
);

// ---------------------------------------------------
// Start Server
// ---------------------------------------------------

httpServer.listen(
    PORT,
    () => {

        console.log(
            "\n========================================"
        );

        console.log(
            `AudioHook Monitor Running on Port ${PORT}`
        );

        console.log(
            "========================================\n"
        );

    }
);