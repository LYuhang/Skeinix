import type {
  AuthenticationResponseJSON,
  PublicKeyCredentialCreationOptionsJSON,
  PublicKeyCredentialRequestOptionsJSON,
  RegistrationResponseJSON,
} from '@/lib/api/mfa';

function decodeBase64Url(value: string): ArrayBuffer {
  const normalized = value.replace(/-/g, '+').replace(/_/g, '/');
  const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, '=');
  const binary = atob(padded);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes.buffer;
}

function encodeBase64Url(value: ArrayBuffer | null): string | null {
  if (value === null) return null;
  const bytes = new Uint8Array(value);
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '');
}

function credentialDescriptors(value: unknown): PublicKeyCredentialDescriptor[] | undefined {
  if (!Array.isArray(value)) return undefined;
  return value.map((item) => {
    const descriptor = item as { id: string; type: PublicKeyCredentialType; transports?: AuthenticatorTransport[] };
    return { ...descriptor, id: decodeBase64Url(descriptor.id) };
  });
}

function creationOptionsFromJson(
  input: PublicKeyCredentialCreationOptionsJSON,
): PublicKeyCredentialCreationOptions {
  const parser = (PublicKeyCredential as unknown as {
    parseCreationOptionsFromJSON?: (
      value: PublicKeyCredentialCreationOptionsJSON,
    ) => PublicKeyCredentialCreationOptions;
  }).parseCreationOptionsFromJSON;
  if (parser) return parser(input);
  const value = input as {
    challenge: string;
    user: { id: string; name: string; displayName: string };
    excludeCredentials?: unknown;
  } & Record<string, unknown>;
  return {
    ...value,
    challenge: decodeBase64Url(value.challenge),
    user: { ...value.user, id: decodeBase64Url(value.user.id) },
    excludeCredentials: credentialDescriptors(value.excludeCredentials),
  } as PublicKeyCredentialCreationOptions;
}

function requestOptionsFromJson(
  input: PublicKeyCredentialRequestOptionsJSON,
): PublicKeyCredentialRequestOptions {
  const parser = (PublicKeyCredential as unknown as {
    parseRequestOptionsFromJSON?: (
      value: PublicKeyCredentialRequestOptionsJSON,
    ) => PublicKeyCredentialRequestOptions;
  }).parseRequestOptionsFromJSON;
  if (parser) return parser(input);
  const value = input as {
    challenge: string;
    allowCredentials?: unknown;
  } & Record<string, unknown>;
  return {
    ...value,
    challenge: decodeBase64Url(value.challenge),
    allowCredentials: credentialDescriptors(value.allowCredentials),
  } as PublicKeyCredentialRequestOptions;
}

function commonCredentialJson(credential: PublicKeyCredential) {
  return {
    id: credential.id,
    rawId: encodeBase64Url(credential.rawId),
    type: credential.type,
    authenticatorAttachment: credential.authenticatorAttachment,
    clientExtensionResults: credential.getClientExtensionResults(),
  };
}

export async function createWebAuthnCredential(
  options: PublicKeyCredentialCreationOptionsJSON,
): Promise<RegistrationResponseJSON> {
  if (!window.isSecureContext || !navigator.credentials || !window.PublicKeyCredential) {
    throw new Error('Passkeys require a secure browser context');
  }
  const credential = await navigator.credentials.create({
    publicKey: creationOptionsFromJson(options),
  });
  if (!(credential instanceof PublicKeyCredential)) {
    throw new Error('The authenticator did not return a public-key credential');
  }
  const response = credential.response as AuthenticatorAttestationResponse;
  return {
    ...commonCredentialJson(credential),
    response: {
      clientDataJSON: encodeBase64Url(response.clientDataJSON),
      attestationObject: encodeBase64Url(response.attestationObject),
      transports: response.getTransports?.() ?? [],
    },
  };
}

export async function getWebAuthnCredential(
  options: PublicKeyCredentialRequestOptionsJSON,
): Promise<AuthenticationResponseJSON> {
  if (!window.isSecureContext || !navigator.credentials || !window.PublicKeyCredential) {
    throw new Error('Passkeys require a secure browser context');
  }
  const credential = await navigator.credentials.get({
    publicKey: requestOptionsFromJson(options),
  });
  if (!(credential instanceof PublicKeyCredential)) {
    throw new Error('The authenticator did not return a public-key credential');
  }
  const response = credential.response as AuthenticatorAssertionResponse;
  return {
    ...commonCredentialJson(credential),
    response: {
      clientDataJSON: encodeBase64Url(response.clientDataJSON),
      authenticatorData: encodeBase64Url(response.authenticatorData),
      signature: encodeBase64Url(response.signature),
      userHandle: encodeBase64Url(response.userHandle),
    },
  };
}
