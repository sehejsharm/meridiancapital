"use client";

/**
 * Browser half of the Face ID sign-in.
 *
 * WebAuthn passes binary as base64url in JSON, so every ArrayBuffer crossing
 * the wire is encoded here and decoded on the way back. Getting this wrong
 * produces a signature that will not verify, with no useful error, so it is
 * kept in one place rather than inlined at each call site.
 */

function b64urlToBytes(value: string): Uint8Array {
  const padded = value.replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(padded + "=".repeat((4 - (padded.length % 4)) % 4));
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i += 1) out[i] = raw.charCodeAt(i);
  return out;
}

function bytesToB64url(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** True when this browser can do platform biometrics at all. */
export async function platformAuthenticatorAvailable(): Promise<boolean> {
  if (typeof window === "undefined" || !window.PublicKeyCredential) return false;
  try {
    return await window.PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable();
  } catch {
    return false;
  }
}

interface ServerOptions {
  challenge: string;
  rp?: { id?: string; name?: string };
  user?: { id: string; name: string; displayName: string };
  pubKeyCredParams?: PublicKeyCredentialParameters[];
  timeout?: number;
  excludeCredentials?: { id: string; type: string; transports?: AuthenticatorTransport[] }[];
  allowCredentials?: { id: string; type: string; transports?: AuthenticatorTransport[] }[];
  authenticatorSelection?: AuthenticatorSelectionCriteria;
  attestation?: AttestationConveyancePreference;
  userVerification?: UserVerificationRequirement;
  rpId?: string;
}

export async function createCredential(options: ServerOptions): Promise<Record<string, unknown>> {
  const publicKey: PublicKeyCredentialCreationOptions = {
    ...(options as unknown as PublicKeyCredentialCreationOptions),
    challenge: b64urlToBytes(options.challenge),
    user: {
      ...(options.user as unknown as PublicKeyCredentialUserEntity),
      id: b64urlToBytes(options.user!.id),
    },
    excludeCredentials: (options.excludeCredentials ?? []).map((c) => ({
      ...c,
      id: b64urlToBytes(c.id),
      type: "public-key" as const,
    })),
  };

  const credential = (await navigator.credentials.create({ publicKey })) as PublicKeyCredential | null;
  if (!credential) throw new Error("the device did not return a credential");
  const response = credential.response as AuthenticatorAttestationResponse;

  return {
    id: credential.id,
    rawId: bytesToB64url(credential.rawId),
    type: credential.type,
    response: {
      clientDataJSON: bytesToB64url(response.clientDataJSON),
      attestationObject: bytesToB64url(response.attestationObject),
    },
    clientExtensionResults: credential.getClientExtensionResults(),
  };
}

export async function getAssertion(options: ServerOptions): Promise<Record<string, unknown>> {
  const publicKey: PublicKeyCredentialRequestOptions = {
    ...(options as unknown as PublicKeyCredentialRequestOptions),
    challenge: b64urlToBytes(options.challenge),
    allowCredentials: (options.allowCredentials ?? []).map((c) => ({
      ...c,
      id: b64urlToBytes(c.id),
      type: "public-key" as const,
    })),
  };

  const credential = (await navigator.credentials.get({ publicKey })) as PublicKeyCredential | null;
  if (!credential) throw new Error("the device did not return an assertion");
  const response = credential.response as AuthenticatorAssertionResponse;

  return {
    id: credential.id,
    rawId: bytesToB64url(credential.rawId),
    type: credential.type,
    response: {
      clientDataJSON: bytesToB64url(response.clientDataJSON),
      authenticatorData: bytesToB64url(response.authenticatorData),
      signature: bytesToB64url(response.signature),
      userHandle: response.userHandle ? bytesToB64url(response.userHandle) : null,
    },
    clientExtensionResults: credential.getClientExtensionResults(),
  };
}

/** Turn a WebAuthn DOMException into something worth showing an operator. */
export function describeWebAuthnError(error: unknown): string {
  if (error instanceof DOMException) {
    switch (error.name) {
      case "NotAllowedError":
        return "Face ID was cancelled or timed out.";
      case "InvalidStateError":
        return "This device is already enrolled.";
      case "SecurityError":
        return "This page's domain does not match the enrolled one.";
      case "NotSupportedError":
        return "This device cannot do biometric sign-in.";
      default:
        return error.message || error.name;
    }
  }
  return error instanceof Error ? error.message : "Face ID failed";
}
