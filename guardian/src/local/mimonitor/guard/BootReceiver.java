package local.mimonitor.guard;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.util.Log;

public final class BootReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context context, Intent intent) {
        if (!Intent.ACTION_BOOT_COMPLETED.equals(intent.getAction()) &&
                !Intent.ACTION_MY_PACKAGE_REPLACED.equals(intent.getAction())) return;
        if (!GuardService.isEnabled(context) || !GuardService.hasPermission(context)) return;
        try {
            context.startForegroundService(new Intent(context, GuardService.class).setAction(GuardService.ENABLE));
        } catch (RuntimeException error) {
            Log.e("MiMonitorGuard", "Cannot restore guard after boot/update", error);
        }
    }
}
