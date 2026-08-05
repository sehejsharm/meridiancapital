import * as LocalAuthentication from 'expo-local-authentication';
import * as SecureStore from 'expo-secure-store';

/**
 * Face ID / fingerprint as a lock on this device.
 *
 * The session token is what the server trusts; biometrics decide whether this
 * phone hands it over. That is the same guarantee a banking app gives — it
 * stops someone holding your unlocked phone, not someone with the disk.
 */

const ENABLED_KEY = 'meridian_biometric_enabled';

export interface BiometricCapability {
  available: boolean;
  enrolled: boolean;
  label: string;          // "Face ID", "Fingerprint", …
}

export async function capability(): Promise<BiometricCapability> {
  try {
    const [hasHardware, isEnrolled, types] = await Promise.all([
      LocalAuthentication.hasHardwareAsync(),
      LocalAuthentication.isEnrolledAsync(),
      LocalAuthentication.supportedAuthenticationTypesAsync(),
    ]);

    let label = 'Biometrics';
    if (types.includes(LocalAuthentication.AuthenticationType.FACIAL_RECOGNITION)) {
      label = 'Face ID';
    } else if (types.includes(LocalAuthentication.AuthenticationType.FINGERPRINT)) {
      label = 'Fingerprint';
    } else if (types.includes(LocalAuthentication.AuthenticationType.IRIS)) {
      label = 'Iris';
    }

    return { available: hasHardware, enrolled: isEnrolled, label };
  } catch {
    return { available: false, enrolled: false, label: 'Biometrics' };
  }
}

export async function isEnabled(): Promise<boolean> {
  try {
    return (await SecureStore.getItemAsync(ENABLED_KEY)) === '1';
  } catch {
    return false;
  }
}

export async function setEnabled(on: boolean): Promise<void> {
  try {
    if (on) await SecureStore.setItemAsync(ENABLED_KEY, '1');
    else await SecureStore.deleteItemAsync(ENABLED_KEY);
  } catch {
    /* storage unavailable — treated as disabled */
  }
}

/** Prompt, returning true only on a verified match. */
export async function authenticate(reason = 'Unlock Meridian'): Promise<boolean> {
  const cap = await capability();
  if (!cap.available || !cap.enrolled) return false;
  try {
    const res = await LocalAuthentication.authenticateAsync({
      promptMessage: reason,
      cancelLabel: 'Use passcode',
      disableDeviceFallback: false,
    });
    return res.success;
  } catch {
    return false;
  }
}
