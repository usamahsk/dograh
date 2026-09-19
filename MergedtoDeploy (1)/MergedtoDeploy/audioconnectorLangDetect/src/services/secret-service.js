export class SecretService {
    // Static map to hold key/secret pairs
    static secrets = new Map();

    // Initialize static secrets map
    static init() {
        SecretService.secrets.set('ApiKey1', 'Secret1');
    }

    getSecretForKey(key) {
        const secretString = SecretService.secrets.get(key) || '';
        return Buffer.from(secretString);
    }
}

// Initialize static data
SecretService.init();
