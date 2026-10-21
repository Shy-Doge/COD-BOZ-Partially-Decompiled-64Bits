package com.ideaworks3d.studio

import android.os.Environment
import android.util.Log
import java.io.File

/**
 * Creates the player settings file on the first start so nobody has to make it by hand:
 *
 *     /sdcard/BlackOpsZombies/config.txt
 *
 * The game reads it (see tools/hook/hook.c in this repository). An existing file is never touched.
 * Called before the native loader starts, once the "all files access" permission is granted.
 */
object BozConfig {
    private const val TAG = "BozConfig"
    private const val DIR_NAME = "BlackOpsZombies"
    private const val FILE_NAME = "config.txt"

    private fun defaultConfig(): String = listOf(
        "# Black Ops Zombies settings. Read when the game starts; lines starting with # are ignored.",
        "",
        "# Multiplayer server: host or host:port (the port defaults to 3074)",
        "multiplayer_server=boz-online.xubi.org",
        "",
        "# Voice chat flag: 0 = off, 1 = on",
        "voice_chat=0",
        "",
        "# Player name: up to 13 characters from A-Z a-z 0-9 space _ . -",
        "# Without a player_name line the game picks its own default name (Player-12345).",
        "#player_name=MyName",
        ""
    ).joinToString("\n")

    @JvmStatic
    fun ensureDefaultConfig() {
        try {
            val dir = File(Environment.getExternalStorageDirectory(), DIR_NAME)
            val file = File(dir, FILE_NAME)
            if (file.exists()) return
            if (!dir.exists() && !dir.mkdirs()) {
                Log.w(TAG, "could not create " + dir.absolutePath)
                return
            }
            file.writeText(defaultConfig())
            Log.i(TAG, "created " + file.absolutePath)
        } catch (e: Exception) {
            Log.w(TAG, "could not create the default settings file", e)
        }
    }
}
