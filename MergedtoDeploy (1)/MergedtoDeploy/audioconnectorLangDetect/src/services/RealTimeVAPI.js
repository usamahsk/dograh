// RealtimeVapi.js

import dotenv from "dotenv";
import WebSocket from "ws";
import axios from "axios";

dotenv.config();

export class RealtimeVapi {

    constructor() {

        this.client = null;

        this.callId = null;
    }

    async initializeClient(config = {}) {

        console.log(
            "Initializing Vapi realtime client..."
        );

        // =========================
        // CREATE VAPI CALL
        // =========================

        const response =
            await axios.post(

                "https://api.vapi.ai/call",

                {
                    assistantId:
                        process.env.VAPI_ASSISTANT_ID,

                    transport: {

                        provider:
                            "vapi.websocket",

                        audioFormat: {

                            format:
                                "mulaw",

                            container:
                                "raw",

                            sampleRate:
                                8000
                        }
                    }
                },

                {
                    headers: {

                        Authorization:
                            `Bearer ${process.env.VAPI_API_KEY}`,

                        "Content-Type":
                            "application/json"
                    }
                }
            );

        const websocketUrl =
            response.data.transport.websocketCallUrl;

        this.callId =
            response.data.id;

        console.log(
            "Vapi Call Created:",
            this.callId
        );

        console.log(
            "Connecting to:",
            websocketUrl
        );

        // =========================
        // CONNECT WEBSOCKET
        // =========================

        const ws =
            new WebSocket(
                websocketUrl
            );

        ws.on(
            "open",

            () => {

                console.log(
                    "Connected to Vapi realtime websocket"
                );
            }
        );

        ws.on(
            "message",

            (data, isBinary) => {

                if (isBinary) {

                   // console.log(
                       // "Received Vapi audio:",
                       // data.length
                   // );

                } else {

                    try {

                        const msg =
                            JSON.parse(
                                data.toString()
                            );

                        console.log(
                            "Vapi Message:",
                            JSON.stringify(msg)
                        );

                    } catch {

                        console.log(
                            "Unknown Vapi text message"
                        );
                    }
                }
            }
        );

        ws.on(
            "error",

            err => {

                console.error(
                    "Vapi WebSocket Error:",
                    err
                );
            }
        );

        ws.on(
            "close",

            () => {

                console.warn(
                    "Vapi websocket closed"
                );
            }
        );

        this.client = ws;

        return ws;
    }

    getClient() {

        return this.client;
    }

    sendAudio(audioBuffer) {

        if (
            this.client &&
            this.client.readyState ===
            WebSocket.OPEN
        ) {

            this.client.send(audioBuffer);
        }
    }

    close() {

        try {

            if (this.client) {

                this.client.send(
                    JSON.stringify({
                        type: "end-call"
                    })
                );

                this.client.close();
            }

        } catch (err) {

            console.error(
                "Error closing Vapi client:",
                err
            );
        }
    }
}