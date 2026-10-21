package com.example.cointrail

import android.Manifest
import android.content.pm.PackageManager
import androidx.core.content.ContextCompat
import com.getcapacitor.JSObject
import com.getcapacitor.Plugin
import com.getcapacitor.PluginCall
import com.getcapacitor.PluginMethod
import com.getcapacitor.annotation.CapacitorPlugin
import com.getcapacitor.annotation.Permission
import com.getcapacitor.annotation.PermissionCallback

@CapacitorPlugin(
    name = "SmsReceiver",
    permissions = [
        Permission(
            alias = "sms",
            strings = [
                Manifest.permission.RECEIVE_SMS,
                Manifest.permission.READ_SMS
            ]
        )
    ]
)
class SmsReceiverPlugin : Plugin() {

    @PluginMethod
    fun checkStatus(call: PluginCall) {
        val ctx = context
        val result = JSObject().apply {
            put("receiveSms", granted(ctx, Manifest.permission.RECEIVE_SMS))
            put("readSms", granted(ctx, Manifest.permission.READ_SMS))
        }
        call.resolve(result)
    }

    @PluginMethod
    fun requestSmsPermissions(call: PluginCall) {
        if (granted(context, Manifest.permission.RECEIVE_SMS) &&
            granted(context, Manifest.permission.READ_SMS)
        ) {
            checkStatus(call)
            return
        }
        requestPermissionForAlias("sms", call, "onPermissionResult")
    }

    @PermissionCallback
    private fun onPermissionResult(call: PluginCall) {
        checkStatus(call)
    }

    private fun granted(ctx: android.content.Context, perm: String): Boolean {
        return ContextCompat.checkSelfPermission(ctx, perm) == PackageManager.PERMISSION_GRANTED
    }
}
