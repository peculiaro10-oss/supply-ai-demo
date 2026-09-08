package com.example.cauldra;

import android.content.SharedPreferences;
import android.os.Build;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyPermanentlyInvalidatedException;
import android.security.keystore.KeyProperties;
import android.util.Base64;

import androidx.annotation.NonNull;
import androidx.biometric.BiometricManager;
import androidx.biometric.BiometricPrompt;
import androidx.fragment.app.FragmentActivity;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.security.MessageDigest;
import java.util.concurrent.Executor;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

@CapacitorPlugin(name = "CauldraBiometric")
public class CauldraBiometricPlugin extends Plugin {
    private static final String KEYSTORE = "AndroidKeyStore";
    private static final String PREFS = "cauldra_biometric_envelopes_v1";
    private static final String KEY_PREFIX = "cauldra_offline_";
    private static final int STRONG = BiometricManager.Authenticators.BIOMETRIC_STRONG;

    @PluginMethod
    public void getStatus(PluginCall call) {
        String scope = requiredScope(call);
        if (scope == null) return;
        int capability = BiometricManager.from(getContext()).canAuthenticate(STRONG);
        boolean available = capability == BiometricManager.BIOMETRIC_SUCCESS;
        boolean enabled = false;
        String reason = capabilityReason(capability);
        try {
            enabled = preferences().contains(prefKey(scope)) && keyStore().containsAlias(alias(scope));
            if (preferences().contains(prefKey(scope)) && !enabled) reason = "invalidated";
        } catch (Exception ignored) {
            reason = "platform_unavailable";
        }
        JSObject result = new JSObject();
        result.put("available", available);
        result.put("enabled", available && enabled);
        result.put("reason", reason);
        call.resolve(result);
    }

    @PluginMethod
    public void enable(PluginCall call) {
        String scope = requiredScope(call);
        String secret = call.getString("secret");
        if (scope == null) return;
        if (secret == null || secret.isEmpty()) { call.reject("Workspace key is required.", "INVALID_INPUT"); return; }
        if (BiometricManager.from(getContext()).canAuthenticate(STRONG) != BiometricManager.BIOMETRIC_SUCCESS) {
            resolveStatus(call, "unavailable", scope, null);
            return;
        }
        runOnMain(call, () -> {
            try {
                deleteNativeEnvelope(scope);
                SecretKey key = createKey(scope);
                Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
                cipher.init(Cipher.ENCRYPT_MODE, key);
                cipher.updateAAD(scope.getBytes(StandardCharsets.UTF_8));
                showPrompt(call, scope, cipher, true, secret);
            } catch (Exception ignored) {
                resolveStatus(call, "unavailable", scope, null);
            }
        });
    }

    @PluginMethod
    public void unlock(PluginCall call) {
        String scope = requiredScope(call);
        if (scope == null) return;
        if (BiometricManager.from(getContext()).canAuthenticate(STRONG) != BiometricManager.BIOMETRIC_SUCCESS) {
            resolveStatus(call, "unavailable", scope, null);
            return;
        }
        runOnMain(call, () -> {
            try {
                String stored = preferences().getString(prefKey(scope), null);
                if (stored == null) { resolveStatus(call, "unavailable", scope, null); return; }
                String[] parts = stored.split(":", 2);
                if (parts.length != 2) { resolveStatus(call, "invalidated", scope, null); return; }
                SecretKey key = (SecretKey) keyStore().getKey(alias(scope), null);
                if (key == null) { resolveStatus(call, "invalidated", scope, null); return; }
                Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
                cipher.init(Cipher.DECRYPT_MODE, key, new GCMParameterSpec(128, decode(parts[0])));
                cipher.updateAAD(scope.getBytes(StandardCharsets.UTF_8));
                showPrompt(call, scope, cipher, false, parts[1]);
            } catch (KeyPermanentlyInvalidatedException ignored) {
                resolveStatus(call, "invalidated", scope, null);
            } catch (Exception ignored) {
                resolveStatus(call, "unavailable", scope, null);
            }
        });
    }

    @PluginMethod
    public void disable(PluginCall call) {
        String scope = requiredScope(call);
        if (scope == null) return;
        try { deleteNativeEnvelope(scope); }
        catch (Exception ignored) {}
        resolveStatus(call, "success", scope, null);
    }

    private void showPrompt(PluginCall call, String scope, Cipher cipher, boolean encrypting, String value) {
        FragmentActivity activity = (FragmentActivity) getActivity();
        Executor executor = activity.getMainExecutor();
        BiometricPrompt prompt = new BiometricPrompt(activity, executor, new BiometricPrompt.AuthenticationCallback() {
            @Override
            public void onAuthenticationSucceeded(@NonNull BiometricPrompt.AuthenticationResult authenticationResult) {
                try {
                    Cipher authenticated = authenticationResult.getCryptoObject() == null ? null : authenticationResult.getCryptoObject().getCipher();
                    if (authenticated == null) { resolveStatus(call, "unavailable", scope, null); return; }
                    if (encrypting) {
                        byte[] encrypted = authenticated.doFinal(decode(value));
                        preferences().edit().putString(prefKey(scope), encode(authenticated.getIV()) + ":" + encode(encrypted)).apply();
                        resolveStatus(call, "success", scope, null);
                    } else {
                        byte[] decrypted = authenticated.doFinal(decode(value));
                        resolveStatus(call, "success", scope, encode(decrypted));
                    }
                } catch (Exception ignored) {
                    resolveStatus(call, "invalidated", scope, null);
                }
            }

            @Override
            public void onAuthenticationError(int errorCode, @NonNull CharSequence errString) {
                if (errorCode == BiometricPrompt.ERROR_NEGATIVE_BUTTON || errorCode == BiometricPrompt.ERROR_USER_CANCELED || errorCode == BiometricPrompt.ERROR_CANCELED) {
                    resolveStatus(call, "cancelled", scope, null);
                } else if (errorCode == BiometricPrompt.ERROR_LOCKOUT || errorCode == BiometricPrompt.ERROR_LOCKOUT_PERMANENT) {
                    resolveStatus(call, "lockout", scope, null);
                } else if (errorCode == BiometricPrompt.ERROR_NO_BIOMETRICS || errorCode == BiometricPrompt.ERROR_HW_NOT_PRESENT || errorCode == BiometricPrompt.ERROR_HW_UNAVAILABLE) {
                    resolveStatus(call, "unavailable", scope, null);
                } else {
                    resolveStatus(call, "unavailable", scope, null);
                }
            }
        });
        BiometricPrompt.PromptInfo promptInfo = new BiometricPrompt.PromptInfo.Builder()
            .setTitle(encrypting ? "Enable biometric Offline Access" : "Unlock Cauldra Offline Access")
            .setSubtitle("Use a strong fingerprint or face enrolled on this device")
            .setAllowedAuthenticators(STRONG)
            .setNegativeButtonText("Use Offline PIN")
            .setConfirmationRequired(true)
            .build();
        prompt.authenticate(promptInfo, new BiometricPrompt.CryptoObject(cipher));
    }

    private SecretKey createKey(String scope) throws Exception {
        KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, KEYSTORE);
        KeyGenParameterSpec.Builder builder = new KeyGenParameterSpec.Builder(alias(scope), KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setUserAuthenticationRequired(true)
            .setInvalidatedByBiometricEnrollment(true);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) builder.setUserAuthenticationParameters(0, KeyProperties.AUTH_BIOMETRIC_STRONG);
        else builder.setUserAuthenticationValidityDurationSeconds(-1);
        generator.init(builder.build());
        return generator.generateKey();
    }

    private String requiredScope(PluginCall call) {
        String scope = call.getString("scope");
        if (scope == null || !scope.matches("^[0-9]+:[0-9]+:[0-9a-fA-F-]{36}$")) {
            call.reject("A valid device-bound workspace scope is required.", "INVALID_SCOPE");
            return null;
        }
        return scope;
    }

    private void runOnMain(PluginCall call, Runnable action) {
        if (!(getActivity() instanceof FragmentActivity)) { resolveStatus(call, "unavailable", call.getString("scope"), null); return; }
        getActivity().runOnUiThread(action);
    }

    private SharedPreferences preferences() {
        return getContext().getSharedPreferences(PREFS, 0);
    }

    private KeyStore keyStore() throws Exception {
        KeyStore store = KeyStore.getInstance(KEYSTORE);
        store.load(null);
        return store;
    }

    private void deleteNativeEnvelope(String scope) throws Exception {
        preferences().edit().remove(prefKey(scope)).apply();
        KeyStore store = keyStore();
        if (store.containsAlias(alias(scope))) store.deleteEntry(alias(scope));
    }

    private String alias(String scope) { return KEY_PREFIX + digest(scope); }
    private String prefKey(String scope) { return "envelope_" + digest(scope); }

    private String digest(String value) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8));
            StringBuilder result = new StringBuilder();
            for (byte item : digest) result.append(String.format("%02x", item));
            return result.toString();
        } catch (Exception error) {
            throw new IllegalStateException("SHA-256 is unavailable", error);
        }
    }

    private String encode(byte[] value) { return Base64.encodeToString(value, Base64.URL_SAFE | Base64.NO_WRAP | Base64.NO_PADDING); }
    private byte[] decode(String value) { return Base64.decode(value, Base64.URL_SAFE | Base64.NO_WRAP | Base64.NO_PADDING); }

    private String capabilityReason(int capability) {
        if (capability == BiometricManager.BIOMETRIC_SUCCESS) return "available";
        if (capability == BiometricManager.BIOMETRIC_ERROR_NONE_ENROLLED) return "not_enrolled";
        if (capability == BiometricManager.BIOMETRIC_ERROR_NO_HARDWARE) return "no_hardware";
        if (capability == BiometricManager.BIOMETRIC_ERROR_HW_UNAVAILABLE) return "hardware_unavailable";
        return "platform_unavailable";
    }

    private void resolveStatus(PluginCall call, String status, String scope, String secret) {
        JSObject result = new JSObject();
        result.put("status", status);
        result.put("scope", scope);
        if (secret != null) result.put("secret", secret);
        call.resolve(result);
    }
}
