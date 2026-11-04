package local.mimonitor.guard;

import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

public final class MainActivity extends Activity {
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        int padding = (int) (32 * getResources().getDisplayMetrics().density);
        layout.setPadding(padding, padding, padding, padding);
        TextView status = new TextView(this);
        status.setTextSize(22);
        status.setText("USB ADB 守护：" + (GuardService.isEnabled(this) ? "开启" : "关闭")
                + "\n只维持调试开关，不连接网络、不采集声音。\n要手动关闭 ADB，请先停用守护。");
        layout.addView(status);
        for (boolean enable : new boolean[] {true, false}) {
            Button button = new Button(this);
            button.setText(enable ? "启用守护" : "停用守护");
            button.setOnClickListener(view -> {
                if (enable && !GuardService.hasPermission(this)) {
                    status.setText("尚未授权，请先在 Mac 上运行守护安装命令。");
                    return;
                }
                startForegroundService(new Intent(this, GuardService.class)
                        .setAction(enable ? GuardService.ENABLE : GuardService.DISABLE));
                status.setText(enable ? "守护已开启。要手动关闭 ADB，请先停用守护。" : "守护已停用，ADB 当前状态保持不变。");
            });
            layout.addView(button);
        }
        setContentView(layout);
    }
}
