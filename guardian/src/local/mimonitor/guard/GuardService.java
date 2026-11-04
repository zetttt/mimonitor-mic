package local.mimonitor.guard;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.content.pm.ServiceInfo;
import android.database.ContentObserver;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.provider.Settings;
import android.util.Log;
import java.io.FileDescriptor;
import java.io.PrintWriter;

public final class GuardService extends Service {
    public static final String ENABLE = "local.mimonitor.guard.ENABLE";
    public static final String DISABLE = "local.mimonitor.guard.DISABLE";
    private static final String TAG = "MiMonitorGuard";
    private final Handler handler = new Handler(Looper.getMainLooper());
    private boolean enabled;
    private boolean observing;
    private int restores;

    private final ContentObserver observer = new ContentObserver(handler) {
        @Override public void onChange(boolean selfChange) { restoreAdb(); }
    };
    private final BroadcastReceiver wake = new BroadcastReceiver() {
        @Override public void onReceive(Context context, Intent intent) { restoreAdb(); }
    };
    private final Runnable check = new Runnable() {
        @Override public void run() {
            restoreAdb();
            // The observer handles changes immediately; polling also covers missed notifications.
            if (enabled) handler.postDelayed(this, 10_000);
        }
    };

    public static boolean isEnabled(Context context) {
        return context.getSharedPreferences("guard", MODE_PRIVATE).getBoolean("enabled", false);
    }

    public static boolean hasPermission(Context context) {
        return context.checkSelfPermission(Manifest.permission.WRITE_SECURE_SETTINGS)
                == PackageManager.PERMISSION_GRANTED;
    }

    private void setEnabled(boolean value) {
        enabled = value;
        getSharedPreferences("guard", MODE_PRIVATE).edit().putBoolean("enabled", value).commit();
    }

    @Override public void onCreate() {
        super.onCreate();
        NotificationManager manager = getSystemService(NotificationManager.class);
        manager.createNotificationChannel(new NotificationChannel("guard", "USB ADB 守护",
                NotificationManager.IMPORTANCE_LOW));
        PendingIntent open = PendingIntent.getActivity(this, 0, new Intent(this, MainActivity.class),
                PendingIntent.FLAG_IMMUTABLE);
        PendingIntent stop = PendingIntent.getService(this, 0,
                new Intent(this, GuardService.class).setAction(DISABLE), PendingIntent.FLAG_IMMUTABLE);
        Notification notification = new Notification.Builder(this, "guard")
                .setSmallIcon(android.R.drawable.ic_lock_lock)
                .setContentTitle("USB ADB 守护正在运行")
                .setContentText("维持调试开关；不连接网络、不采集声音")
                .setContentIntent(open).setOngoing(true)
                .addAction(new Notification.Action.Builder(null, "停用守护", stop).build()).build();
        startForeground(1, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
    }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent == null ? null : intent.getAction();
        if (DISABLE.equals(action) || (!ENABLE.equals(action) && !isEnabled(this))) {
            setEnabled(false);
            stopSelf();
            return START_NOT_STICKY;
        }
        if (!hasPermission(this)) {
            Log.e(TAG, "WRITE_SECURE_SETTINGS not granted; guard stopped");
            setEnabled(false);
            stopSelf();
            return START_NOT_STICKY;
        }
        setEnabled(true);
        if (!observing) {
            getContentResolver().registerContentObserver(
                    Settings.Global.getUriFor(Settings.Global.ADB_ENABLED), false, observer);
            registerReceiver(wake, new IntentFilter(Intent.ACTION_SCREEN_ON), RECEIVER_NOT_EXPORTED);
            observing = true;
            handler.post(check);
        }
        return START_STICKY;
    }

    private void restoreAdb() {
        if (!enabled) return;
        try {
            if (Settings.Global.getInt(getContentResolver(), Settings.Global.ADB_ENABLED, 0) != 1) {
                // This runs on the monitor: the Mac cannot reopen ADB after its USB link disappears.
                if (!Settings.Global.putInt(getContentResolver(), Settings.Global.ADB_ENABLED, 1)) {
                    throw new IllegalStateException("ADB setting write was rejected");
                }
                restores++;
                Log.i(TAG, "Restored ADB switch");
            }
        } catch (SecurityException | IllegalStateException error) {
            Log.e(TAG, "Cannot restore ADB; guard stopped", error);
            setEnabled(false);
            stopSelf();
        }
    }

    @Override public void onDestroy() {
        enabled = false;
        handler.removeCallbacksAndMessages(null);
        if (observing) {
            getContentResolver().unregisterContentObserver(observer);
            unregisterReceiver(wake);
        }
        super.onDestroy();
    }

    @Override protected void dump(FileDescriptor fd, PrintWriter writer, String[] args) {
        writer.println("guard_enabled=" + enabled);
        writer.println("adb_enabled=" + Settings.Global.getInt(getContentResolver(), Settings.Global.ADB_ENABLED, 0));
        writer.println("restores=" + restores);
    }

    @Override public IBinder onBind(Intent intent) { return null; }
}
