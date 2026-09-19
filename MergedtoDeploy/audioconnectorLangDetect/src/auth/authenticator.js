import {
    verifySignature,
    withFailure,
    queryCanonicalizedHeaderField
} from './signature-verifier.js';
import { SecretService } from '../services/secret-service.js';

export function verifyRequestSignature(request, secretService) {
    return verifyRequestSignatureImpl(request, secretService);
}

async function verifyRequestSignatureImpl(request, secretService) {
    const apiKey = queryCanonicalizedHeaderField(request.headers, 'x-api-key');

    if (!apiKey) {
        return withFailure('PRECONDITION', 'Missing "X-API-KEY" header field');
    }

    const result = await verifySignature({
        headerFields: request.headers,
        requiredComponents: [
            '@request-target',
            '@authority',
            'audiohook-organization-id',
            'audiohook-session-id',
            'audiohook-correlation-id',
            'x-api-key'
        ],
        maxSignatureAge: 10,
        derivedComponentLookup: (name) => {
            if (name === '@request-target') {
                return request.url || null;
            }
            return null;
        },
        keyResolver: async (parameters) => {
            if (!parameters.nonce) {
                return withFailure('PRECONDITION', 'Missing "nonce" signature parameter');
            } else if (parameters.nonce.length < 22) {
                return withFailure('PRECONDITION', 'Provided "nonce" signature parameter is too small');
            }

            const keyId = parameters.keyid;

            if (keyId !== apiKey) {
                return withFailure('PRECONDITION', 'X-API-KEY header field and signature keyid mismatch');
            }

            const secret = secretService.getSecretForKey(keyId);
            const code = secret ? 'GOODKEY' : 'BADKEY';

            return {
                code: code,
                key: secret
            };
        }
    });

    if (result.code === 'UNSIGNED') {
        return { code: 'VERIFIED' };
    }

    return result;
}
