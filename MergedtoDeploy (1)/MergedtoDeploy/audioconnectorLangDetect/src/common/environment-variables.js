const DEFAULT_PORT = 8080;

export function getPort() {
    const envPort = process.env.PORT;

    if (envPort) {
        return Number(envPort);
    }

    return DEFAULT_PORT;
}
