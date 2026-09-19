import { WebSocketServer, WebSocket } from "ws";
import dotenv from "dotenv";

dotenv.config();

// =====================================================
// GENESYS MOCK + DEEPGRAM PROXY SERVER
// =====================================================

const wss = new WebSocketServer({
    port: 8080
});

console.log(
    "Proxy Server running on port 8080"
);

wss.on("connection", (genesysWs, req) => {

    console.log(
        "Genesys Connected"
    );

    console.log(
        "URL:",
        req.url
    );

    let deepgramWs = null;

    let sessionId = null;

    let lastClientSeq = 0;
    let lastServerSeq = 0;

    // =================================================
    // RECEIVE FROM GENESYS
    // =================================================

    genesysWs.on(
        "message",
        async (data, isBinary) => {

            // =========================================
            // AUDIO FROM GENESYS
            // =========================================

            if (isBinary) {

                console.log(
                    "Genesys Audio:",
                    data.length,
                    "bytes"
                );

                if (
                    deepgramWs &&
                    deepgramWs.readyState === WebSocket.OPEN
                ) {

                    deepgramWs.send(data);
                }

                return;
            }

            try {

                const msg =
                    JSON.parse(
                        data.toString()
                    );

                console.log(
                    "GENESYS MESSAGE:"
                );

                console.log(
                    JSON.stringify(
                        msg,
                        null,
                        2
                    )
                );

                // =====================================
                // OPEN
                // =====================================

                if (msg.type === "open") {

                    sessionId = msg.id;

                    lastClientSeq = msg.seq;

                    // ---------------------------------
                    // SEND OPENED BACK TO GENESYS
                    // ---------------------------------

                    const openedResponse = {

                        version: "2",

                        type: "opened",

                        id: sessionId,

                        seq: ++lastServerSeq,

                        clientseq:
                            lastClientSeq,

                        parameters: {

                            startPaused: false,

                            media:
                                msg.parameters.media
                        }
                    };

                    genesysWs.send(
                        JSON.stringify(
                            openedResponse
                        )
                    );

                    console.log(
                        "SENT OPENED RESPONSE TO GENESYS"
                    );

                    // =================================
                    // CONNECT TO DEEPGRAM
                    // =================================

                    deepgramWs = new WebSocket(
                        "wss://agent.deepgram.com/v1/agent/converse",
                        {
                            headers: {
                                Authorization:
                                    `Token ${process.env.DEEPGRAM_API_KEY}`
                            }
                        }
                    );

                    deepgramWs.binaryType =
                        "arraybuffer";

                    // ================================
                    // DEEPGRAM OPEN
                    // ================================

                    deepgramWs.on(
                        "open",
                        () => {

                            console.log(
                                "Connected to Deepgram"
                            );

                            // ------------------------
                            // GET CONFIG FROM GENESYS
                            // ------------------------

                            let config =
                                msg.parameters
                                    .inputVariables
                                    ?.config;

                            // ------------------------
                            // FALLBACK CONFIG
                            // ------------------------

                            if (!config) {

                                config =
                                    JSON.stringify({

                                        type:
                                            "Settings",

                                        audio: {

                                            input: {
                                                encoding:
                                                    "mulaw",

                                                sample_rate:
                                                    8000
                                            },

                                            output: {
                                                encoding:
                                                    "mulaw",

                                                sample_rate:
                                                    8000,

                                                container:
                                                    "none"
                                            }
                                        },

                                        agent: {

                                            language:
                                                "en",

                                            greeting:
                                                "Hello! How can I help you today?",

                                            listen: {

                                                provider:
                                                    {
                                                        type:
                                                            "deepgram",

                                                        model:
                                                            "nova-3"
                                                    }
                                            },

                                            think: {

                                                provider:
                                                    {
                                                        type:
                                                            "open_ai",

                                                        model:
                                                            "gpt-4o-mini"
                                                    },

                                                prompt:
                                                    "You are a helpful assistant."
                                            },

                                            speak: {

                                                provider:
                                                    {
                                                        type:
                                                            "deepgram",

                                                        model:
                                                            "aura-2-thalia-en"
                                                    }
                                            }
                                        }
                                    });
                            }

                            console.log(
                                "FORWARDING CONFIG TO DEEPGRAM"
                            );

                            console.log(
                                config
                            );

                            // ------------------------
                            // SEND CONFIG TO DEEPGRAM
                            // ------------------------

                            deepgramWs.send(
                                config
                            );
                        }
                    );

                    // ================================
                    // RECEIVE FROM DEEPGRAM
                    // ================================

                    deepgramWs.on(
                        "message",
                        async (
                            dgData,
                            isBinaryDG
                        ) => {

                            // ------------------------
                            // AUDIO FROM DEEPGRAM
                            // ------------------------

                            if (
                                isBinaryDG ||
                                dgData instanceof Buffer
                            ) {

                                console.log(
                                    "Deepgram Audio:",
                                    dgData.length,
                                    "bytes"
                                );

                                genesysWs.send(
                                    dgData,
                                    { binary: true }
                                );

                                return;
                            }

                            // ------------------------
                            // TEXT EVENTS
                            // ------------------------

                            try {

                                const dgMsg =
                                    JSON.parse(
                                        dgData.toString()
                                    );

                                console.log(
                                    "DEEPGRAM MESSAGE:"
                                );

                                console.log(
                                    JSON.stringify(
                                        dgMsg,
                                        null,
                                        2
                                    )
                                );

                                // ====================
                                // HANDLE PING
                                // ====================

                                if (
                                    dgMsg.type ===
                                    "ping"
                                ) {

                                    const pong =
                                        {

                                            type:
                                                "pong",

                                            version:
                                                "2",

                                            id:
                                                dgMsg.id,

                                            seq:
                                                ++lastServerSeq,

                                            clientseq:
                                                lastClientSeq,

                                            parameters:
                                                {}
                                        };

                                    deepgramWs.send(
                                        JSON.stringify(
                                            pong
                                        )
                                    );

                                    console.log(
                                        "PONG SENT TO DEEPGRAM"
                                    );
                                }

                                // ====================
                                // TRANSCRIPTS
                                // ====================

                                if (
                                    dgMsg.type ===
                                    "ConversationText"
                                ) {

                                    const transcriptEvent =
                                        {

                                            type:
                                                "event",

                                            version:
                                                "2",

                                            id:
                                                sessionId,

                                            seq:
                                                ++lastServerSeq,

                                            clientseq:
                                                lastClientSeq,

                                            parameters:
                                                {

                                                    entities:
                                                        [
                                                            {
                                                                type:
                                                                    "transcript",

                                                                data:
                                                                    {

                                                                        channel:
                                                                            "external",

                                                                        isFinal:
                                                                            true,

                                                                        alternatives:
                                                                            [
                                                                                {
                                                                                    confidence:
                                                                                        1.0,

                                                                                    interpretations:
                                                                                        [
                                                                                            {
                                                                                                type:
                                                                                                    "normalized",

                                                                                                transcript:
                                                                                                    dgMsg.content
                                                                                            }
                                                                                        ]
                                                                                }
                                                                            ]
                                                                    }
                                                            }
                                                        ]
                                                }
                                        };

                                    genesysWs.send(
                                        JSON.stringify(
                                            transcriptEvent
                                        )
                                    );
                                }

                            } catch (err) {

                                console.error(
                                    "Deepgram Parse Error:",
                                    err
                                );
                            }
                        }
                    );

                    // ================================
                    // DEEPGRAM ERROR
                    // ================================

                    deepgramWs.on(
                        "error",
                        (err) => {

                            console.error(
                                "Deepgram Error:",
                                err
                            );
                        }
                    );

                    // ================================
                    // DEEPGRAM CLOSE
                    // ================================

                    deepgramWs.on(
                        "close",
                        () => {

                            console.log(
                                "Deepgram Connection Closed"
                            );
                        }
                    );
                }

                // =====================================
                // PING
                // =====================================

                else if (msg.type === "ping") {

                    lastClientSeq = msg.seq;

                    const pongResponse = {

                        version: "2",

                        type: "pong",

                        id: sessionId,

                        seq: ++lastServerSeq,

                        clientseq:
                            lastClientSeq,

                        parameters: {}
                    };

                    genesysWs.send(
                        JSON.stringify(
                            pongResponse
                        )
                    );

                    console.log(
                        "PONG SENT TO GENESYS"
                    );
                }

                // =====================================
                // CLOSE
                // =====================================

                else if (msg.type === "close") {

                    lastClientSeq = msg.seq;

                    const closedResponse = {

                        version: "2",

                        type: "closed",

                        id: sessionId,

                        seq: ++lastServerSeq,

                        clientseq:
                            lastClientSeq,

                        parameters: {}
                    };

                    genesysWs.send(
                        JSON.stringify(
                            closedResponse
                        )
                    );

                    console.log(
                        "CLOSED SENT TO GENESYS"
                    );

                    if (deepgramWs) {

                        deepgramWs.close();
                    }

                    genesysWs.close();
                }

            } catch (err) {

                console.error(
                    "Genesys Parse Error:",
                    err
                );
            }
        }
    );

    // =================================================
    // GENESYS CLOSE
    // =================================================

    genesysWs.on("close", () => {

        console.log(
            "Genesys Connection Closed"
        );

        if (deepgramWs) {

            deepgramWs.close();
        }
    });

    // =================================================
    // GENESYS ERROR
    // =================================================

    genesysWs.on("error", (err) => {

        console.error(
            "Genesys WS Error:",
            err
        );
    });
});