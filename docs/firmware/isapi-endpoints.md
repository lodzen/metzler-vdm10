# ISAPI endpoints registered by `hicore` (VDM10 indoor, fw 2.0.24)

Complete catalog of the HTTP-XML (ISAPI) routes compiled into this unit's
`hicore` binary — recovered by walking the URI routing table in `.data`
(`fw_extracted/trees/vistalk/hicore`), not from generic Hikvision
documentation. Reproduce with `tools/dump_isapi_table.py`.

Table format (verified): each route is a `0x68`-byte record
`{name* @+0, handler* @+4, unused @+8, childTbl* @+0xc, one @+0x10,
methodFlags @+0x14, …zero pad}`; rows of a table are contiguous and a
`/<parent>`-named row terminates it. `childTbl` points at a nested table;
`index`-named rows are `/<id>` parameterized-segment placeholders — all
inert in this build (handler `0`, no children): parent dispatchers parse
numeric ids out of the path themselves.

**methodFlags @+0x14** (inferred): bit0 = GET, bit2 = PUT, bit4 = row has
a parameterized child table; bit1 = a second write verb († POST or
DELETE — dispatchers compare the method id in `param_2[2]`, where 1=GET
and 4=PUT are confirmed). `⁺` = has `/index` child. Nonzero flags on
handler-less dir rows likely still describe the subtree's verbs.

Handler column = registered dispatcher vaddr. Names resolve via
`vdm10_1.0/names/isapi.tsv` + `vdm10_1.0/isapi/*.c` provenance
(`/* 0x<addr>`); most handlers are 4-8-byte entry stubs
(`cmp r1,#0; movne r0,#0; push`) that fall through to the named body.
Empty name = no recovered symbol nearby.

**11 groups, 189 rows, 146 dispatcher endpoints**:
`System`, `VideoIntercom`, `Event`, `Security`, `Image`,
`AccessControl`, `Streaming`, `SecurityCP`, `ContentMgmt`, `SmartLock`,
`SingleSmartLock`

## /ISAPI/System

| Method | Path | handler | name (rewrite) | notes |
|---|---|---|---|---|
| PUT | `/ISAPI/System/activate` | `0x2168b4` | `isapiSysActivateBasic` | first-boot admin activation |
| GET | `/ISAPI/System/capabilities` | `0x21677c` | `isapiSysCapBasic` |  |
| PUT | `/ISAPI/System/reboot` | `0x216e10` | `isapiSysRebootBasic` |  |
| PUT | `/ISAPI/System/updateFirmware` | `0x217d50` | `isapi_sysUpgradeBasic` | DAV upload upgrade |
| GET+PUT | `/ISAPI/System/configurationData` | `0x217000` | `isapiSysConfigurationDataBasic` |  |
| PUT | `/ISAPI/System/factoryReset` | `0x217428` | `isapiSysFactoryResetBasic` |  |
| GET+PUT | `/ISAPI/System/deviceInfo` | `0x217690` | `isapiSysDeviceInfoBasic` |  |
| GET | `/ISAPI/System/upgradeStatus` | `0x217c20` | `isapiSysUpgradeStatusBasic` |  |
| GET+PUT | `/ISAPI/System/time` | `0x21da04` |  |  |
| GET | `/ISAPI/System/time/capabilities` | `0x21c32c` | `isapiSysTimeCapBasic` |  |
| GET+PUT | `/ISAPI/System/time/localTime` | `0x21c9c8` | `isapiSysTimeLocaltimeBasic` |  |
| GET+PUT | `/ISAPI/System/time/timeZone` | `0x21e32c` |  |  |
| GET+POST/DEL†+PUT ⁺ | `/ISAPI/System/time/ntpServers` | `0x21bab4` |  |  |
| GET | `/ISAPI/System/time/ntpServers/capabilities` | `0x21bdd4` |  |  |
|  | `/ISAPI/System/Network` ▸ |  |  |  |
| GET | `/ISAPI/System/Network/capabilities` | `0x215580` | `isapiSysNetworkCapBasic` |  |
| GET | `/ISAPI/System/Network/interfaces` | `0x213f84` | `isapiSysNetworkInterfacesBasic` |  |
|  | `/ISAPI/System/Network/interfaces/index` |  |  |  |
| GET | `/ISAPI/System/Network/interfaces/capabilities` | `0x214094` | `isapiSysNetworkInterfacesCapBasic` |  |
| GET+PUT | `/ISAPI/System/Network/ftp` | `0x2149ec` | `isapiSysNetworkFtpBasic` |  |
| GET | `/ISAPI/System/Network/ftp/capabilities` | `0x214c20` | `isapiSysNetworkFtpCapBasic` |  |
| GET+PUT | `/ISAPI/System/Network/mailing` | `0x215674` | `isapiSysNetworkMailingBasic` |  |
| GET | `/ISAPI/System/Network/mailing/capabilities` | `0x215838` | `isapiSysNetworkMailingCapBasic` |  |
|  | `/ISAPI/System/Audio` ▸ |  |  |  |
| GET | `/ISAPI/System/Audio/capabilities` | `0x205378` |  |  |
|  | `/ISAPI/System/Audio/AudioIn` ▸ |  |  |  |
|  | `/ISAPI/System/Audio/AudioIn/channels` ▸ |  |  |  |
|  | `/ISAPI/System/Audio/AudioIn/channels/index` |  |  |  |
|  | `/ISAPI/System/Audio/AudioOut` ▸ |  |  |  |
|  | `/ISAPI/System/Audio/AudioOut/channels` ▸ |  |  |  |
|  | `/ISAPI/System/Audio/AudioOut/channels/index` |  |  |  |
|  | `/ISAPI/System/TwoWayAudio` ▸ |  |  |  |
| GET | `/ISAPI/System/TwoWayAudio/index` |  |  |  |
| GET+PUT | `/ISAPI/System/TwoWayAudio/channels` | `0x20330c` |  |  |
| GET | `/ISAPI/System/TwoWayAudio/channels/index` |  |  |  |
| GET | `/ISAPI/System/TwoWayAudio/channels/capabilities` | `0x2037b4` |  |  |
|  | `/ISAPI/System/Video` ▸ |  |  |  |
| GET | `/ISAPI/System/Video/capabilities` | `0x1dad6c` |  |  |
| GET | `/ISAPI/System/Video/inputs` | `0x1d9ee4` |  |  |
| GET | `/ISAPI/System/Video/inputs/channels` | `0x1da07c` |  |  |
|  | `/ISAPI/System/Video/inputs/channels/index` |  |  |  |
| GET | `/ISAPI/System/IO` | `0x20e71c` |  |  |
| GET | `/ISAPI/System/IO/capabilities` | `0x20e2b4` |  |  |
| GET | `/ISAPI/System/IO/inputs` | `0x20e580` |  |  |
|  | `/ISAPI/System/IO/inputs/index` |  |  |  |
| GET | `/ISAPI/System/IO/outputs` | `0x20d7ec` |  | IO relay/lock outputs |
|  | `/ISAPI/System/IO/outputs/index` |  |  |  |
| GET+PUT | `/ISAPI/System/DeviceLanguage` | `0x217a10` | `isapiSysLanguageBasic` |  |
| GET | `/ISAPI/System/DeviceLanguage/capabilities` | `0x2178e8` | `isapiSysLanguageCapBasic` |  |
| GET | `/ISAPI/System/DeviceLanguage/index` |  |  |  |
| GET+PUT | `/ISAPI/System/DeviceLanguage/channels` | `0x20330c` |  |  |
| GET | `/ISAPI/System/DeviceLanguage/channels/index` |  |  |  |
| GET | `/ISAPI/System/DeviceLanguage/channels/capabilities` | `0x2037b4` |  |  |
| GET | `/ISAPI/System/onlineUpgrade` |  |  |  |
| GET | `/ISAPI/System/onlineUpgrade/server` | `0x216034` | `isapi_srv_online_upgrade_server` |  |
| GET | `/ISAPI/System/onlineUpgrade/version` | `0x21612c` | `isapi_srv_online_upgrade_version` |  |
| PUT | `/ISAPI/System/onlineUpgrade/upgrade` | `0x216358` | `isapi_srv_online_upgrade` |  |
| PUT | `/ISAPI/System/onlineUpgrade/judgeVersion` | `0x2164b8` | `isapi_srv_online_upgrade_judge_ver` |  |
| GET | `/ISAPI/System/onlineUpgrade/status` | `0x21672c` | `isapi_srv_online_upgrade_status` |  |
| GET | `/ISAPI/System/firmwareCode` | `0x215da0` | `isapi_srv_firmware_code_list` |  |

## /ISAPI/VideoIntercom

| Method | Path | handler | name (rewrite) | notes |
|---|---|---|---|---|
| GET | `/ISAPI/VideoIntercom/capabilities` | `0x1cd924` | `isapiIntercomCapBasic` |  |
| GET | `/ISAPI/VideoIntercom/PrivilegePasswordStatus` | `0x1cd830` | `isapiPrivilegePasswordStatusBasic` |  |
| GET+PUT | `/ISAPI/VideoIntercom/SystemSwitchCfg` | `0x1cd4f8` | `isapiSystemSwitchBasic` |  |
| GET | `/ISAPI/VideoIntercom/SystemSwitchCfg/capabilities` | `0x1cd708` | `isapiSystemTypeCapBasic` |  |
| GET | `/ISAPI/VideoIntercom/SystemSwitchCfg/capabilities` | `0x1d1bb0` | `isapiIntercomIncomingCallLinkageCapBasic` |  |
|  | `/ISAPI/VideoIntercom/SystemSwitchCfg/index` |  |  |  |
| GET+PUT | `/ISAPI/VideoIntercom/deviceId` | `0x1cdf88` | `isapiIntercomDeviceIdBasic` |  |
| GET | `/ISAPI/VideoIntercom/deviceId/capabilities` | `0x1cddec` | `isapiIntercomDeviceIdCapBasic` |  |
| GET+PUT | `/ISAPI/VideoIntercom/operationTime` | `0x1ce1f0` | `isapiIntercomOperationTimeBasic` |  |
| GET | `/ISAPI/VideoIntercom/operationTime/capabilities` | `0x1ce44c` | `isapiIntercomOperationTimeCapBasic` |  |
| GET+PUT | `/ISAPI/VideoIntercom/relatedDeviceAddress` | `0x1ce5c0` | `isapiIntercomRelatedDeviceAddrBasic` |  |
| GET | `/ISAPI/VideoIntercom/relatedDeviceAddress/capabilities` | `0x1ce874` | `isapiIntercomRelatedDeviceAddrCapBasic` |  |
| PUT | `/ISAPI/VideoIntercom/passwordAuthentication` | `0x1cea04` | `isapiIntercomPwdAuthBasic` |  |
| GET | `/ISAPI/VideoIntercom/passwordAuthentication/capabilities` | `0x1cebd4` | `isapiIntercomPwdAuthCapBasic` |  |
| GET | `/ISAPI/VideoIntercom/keyCfg` | `0x1cf114` | `isapiIntercomKeyCfgBasic` | touch-key config |
|  | `/ISAPI/VideoIntercom/keyCfg/index` |  |  |  |
| PUT | `/ISAPI/VideoIntercom/remoteOpenDoor` | `0x1ced50` | `isapiIntercomRemoteOpenDoorBasic` | **gated**: devType∈{1,4,5} — room unit (3) replies `notSupport` |
| GET | `/ISAPI/VideoIntercom/remoteOpenDoor/capabilities` | `0x1cef7c` | `isapiIntercomRemoteOpenDoorCapBasic` |  |
| GET ⁺ | `/ISAPI/VideoIntercom/ring` | `0x1cfa2c` | `isapiIntercomRingBasic` |  |
| GET | `/ISAPI/VideoIntercom/ring/capabilities` | `0x1cf8a0` | `isapiIntercomRingCapBasic` |  |
|  | `/ISAPI/VideoIntercom/ring/ringID` ▸ |  |  |  |
|  | `/ISAPI/VideoIntercom/ring/ringID/index` |  |  |  |
|  | `/ISAPI/VideoIntercom/SmartHome` ▸ |  |  |  |
| GET+POST/DEL† ⁺ | `/ISAPI/VideoIntercom/SmartHome/rooms` | `0x1e77d4` | `isapiSmartHomeRoomBasic` |  |
| GET | `/ISAPI/VideoIntercom/SmartHome/rooms/capabilities` | `0x1e7d50` | `isapiSmartHomeRoomCapBasic` |  |
| GET+POST/DEL† ⁺ | `/ISAPI/VideoIntercom/SmartHome/devices` | `0x1eeda4` | `isapiSmartHomeDeviceListBasic` |  |
| GET | `/ISAPI/VideoIntercom/SmartHome/devices/capabilities` | `0x1ef6a0` | `isapiSmartHomeDeviceCapBasic` |  |
| POST/DEL† | `/ISAPI/VideoIntercom/SmartHome/deviceSearch` | `0x1ef83c` | `isapiSmartHomeSmartDeviceSearchBasic` |  |
| GET | `/ISAPI/VideoIntercom/SmartHome/deviceSearch/profile` | `0x1efb1c` | `isapiSmartHomeDevSearchExtBasic` |  |
| GET+POST/DEL† ⁺ | `/ISAPI/VideoIntercom/SmartHome/scenes` | `0x1ea554` | `isapiSmartHomeSceneBasic` |  |
| GET | `/ISAPI/VideoIntercom/SmartHome/scenes/capabilities` | `0x1ead2c` | `isapiSmartHomeSceneIdBasic` |  |
| PUT | `/ISAPI/VideoIntercom/QRCodeCfg` | `0x1d0738` | `isapiIntercomSetQR` |  |
| GET | `/ISAPI/VideoIntercom/QRCodeInfo` | `0x1cda5c` | `isapiIntercomGetQRInfoBasic` |  |
|  ⁺ | `/ISAPI/VideoIntercom/scene` |  |  |  |
| GET+PUT | `/ISAPI/VideoIntercom/scene/nowMode` | `0x1cff5c` | `isapiIntercomCurrentSceneModeBasic` |  |
| GET | `/ISAPI/VideoIntercom/scene/nowMode/capabilities` | `0x1d031c` | `isapiIntercomCurrentSceneModeCapBasic` |  |
|  | `/ISAPI/VideoIntercom/SmartLock` ▸ |  |  |  |
| GET | `/ISAPI/VideoIntercom/SmartLock/lockMatched` | `0x1f7cc4` | `isapiIntercomSmartLockMatchedBasic` |  |
| GET | `/ISAPI/VideoIntercom/SmartLock/lockMatched/capabilities` | `0x1f80e4` | `isapiIntercomSmartLockMatchedBasicCap` |  |
|  | `/ISAPI/VideoIntercom/SmartLock/index` |  |  |  |
| POST/DEL† | `/ISAPI/VideoIntercom/OpenDoorLog` | `0x1fd6a0` | `isapiSmartLockStateExtBasic` | door-open record search |
| GET | `/ISAPI/VideoIntercom/OpenDoorLog/capabilities` | `0x1fe188` |  |  |
| PUT | `/ISAPI/VideoIntercom/callElevator` | `0x1d0518` | `isapiIntercomCallElevatorBasic` | lift control |
| GET+PUT | `/ISAPI/VideoIntercom/AnnouncementMessage` | `0x1d0928` | `isapiIntercomAnnouncementMessageBasic` |  |
| GET | `/ISAPI/VideoIntercom/AnnouncementMessage/capabilities` | `0x1d0c2c` | `isapiIntercomAnnouncementMessageCapBasic` |  |
| GET+PUT | `/ISAPI/VideoIntercom/AppKeyConfiguration` | `0x1d0db8` | `isapiIntercomAppKeyConfigurationBasic` |  |
| GET | `/ISAPI/VideoIntercom/AppKeyConfiguration/capabilities` | `0x1d1028` | `isapiIntercomAppKeyConfigurationCapBasic` |  |
| GET+PUT | `/ISAPI/VideoIntercom/alarmUploadCfg` | `0x1d11f0` | `isapiIntercomAlarmUploadCfgBasic` |  |
| GET | `/ISAPI/VideoIntercom/alarmUploadCfg/capabilities` | `0x1d14d4` | `isapiIntercomAlarmUploadCfgCapBasic` |  |
| PUT | `/ISAPI/VideoIntercom/PlateInfo` | `0x1cdbcc` | `isapiIntercomPlateInfoBasic` |  |
| GET+PUT | `/ISAPI/VideoIntercom/Linkage` |  |  |  |
| GET+PUT | `/ISAPI/VideoIntercom/Linkage/IncomingCall` | `0x1d1a20` | `isapiIntercomIncomingCallLinkageBasic` |  |
| GET | `/ISAPI/VideoIntercom/Linkage/IncomingCall/capabilities` | `0x1d1bb0` | `isapiIntercomIncomingCallLinkageCapBasic` |  |
|  | `/ISAPI/VideoIntercom/Linkage/IncomingCall/index` |  |  |  |
|  | `/ISAPI/VideoIntercom/Linkage/index` |  |  |  |
| GET+PUT | `/ISAPI/VideoIntercom/IntercomProtocolType` | `0x1d16b0` | `isapiIntercomSipTypeBasic` |  |
| GET | `/ISAPI/VideoIntercom/IntercomProtocolType/capabilities` | `0x1d18dc` | `isapiIntercomSipTypeCapBasic` |  |

## /ISAPI/Event

| Method | Path | handler | name (rewrite) | notes |
|---|---|---|---|---|
|  | `/ISAPI/Event/index` |  |  |  |
| GET | `/ISAPI/Event/capabilities` | `0x209cd4` | `isapiEventCapabilities` |  |
| GET+POST/DEL†+PUT ⁺ | `/ISAPI/Event/triggers` | `0x20a18c` | `isapiEventTriggerBasic` |  |
|  | `/ISAPI/Event/triggers/index` |  |  |  |
| GET+PUT | `/ISAPI/Event/schedules` |  |  |  |
|  | `/ISAPI/Event/schedules/index` |  |  |  |
| GET | `/ISAPI/Event/schedules/motionDetections` | `0x20ae24` | `isapiMotionDetSchedulesBasic` |  |
|  | `/ISAPI/Event/schedules/motionDetections/index` |  |  |  |

## /ISAPI/Security

| Method | Path | handler | name (rewrite) | notes |
|---|---|---|---|---|
| GET | `/ISAPI/Security/capabilities` | `0x1c7478` | `isapiSecurityCapBasic` |  |
| GET+POST/DEL†+PUT ⁺ | `/ISAPI/Security/users` | `0x1c7574` | `isapiSecurityUsersBasic` |  |
|  | `/ISAPI/Security/users/index` |  |  |  |
| GET+POST/DEL†+PUT | `/ISAPI/Security/adminAccesses` | `0x1c810c` | `isapiSecurityAdminAccessBasic` |  |
|  | `/ISAPI/Security/adminAccesses/index` |  |  |  |
| GET | `/ISAPI/Security/adminAccesses/capabilities` | `0x1c83a0` | `isapiSecurityAdminAccessCapBasic` |  |
| GET+POST/DEL†+PUT | `/ISAPI/Security/userCheck` | `0x1c8898` | `isapiSecurityUserCheckReturn` | digest login verification |
| POST/DEL†+PUT | `/ISAPI/Security/challenge` | `0x1c7a28` | `isapi_getChallenge` | RSA challenge for client-side digest |
| GET+PUT | `/ISAPI/Security/UserPermission` | `0x1c8fac` | `hikUserPermissionBasic` |  |
| GET | `/ISAPI/Security/UserPermission/viewerCap` |  |  |  |
| GET | `/ISAPI/Security/UserPermission/operatorCap` |  |  |  |

## /ISAPI/Image

| Method | Path | handler | name (rewrite) | notes |
|---|---|---|---|---|
|  | `/ISAPI/Image/channels` ▸ |  |  |  |
|  | `/ISAPI/Image/channels/index` |  |  |  |
|  | `/ISAPI/Image/channels/channels` ▸ |  |  |  |

## /ISAPI/AccessControl

| Method | Path | handler | name (rewrite) | notes |
|---|---|---|---|---|
| GET+PUT | `/ISAPI/AccessControl/DeployInfo` | `0x1e4c30` | `isapiDeployInfoBasic` |  |
| GET | `/ISAPI/AccessControl/DeployInfo/capabilities` | `0x1e4ac0` | `isapiDeployInfoCapBasic` |  |
| GET | `/ISAPI/AccessControl/DeployInfo/param` | `0x1e39ac` | `isapiAcsCtrlDoorParamBasic` |  |
|  | `/ISAPI/AccessControl/DeployInfo/param/index` |  |  |  |
|  | `/ISAPI/AccessControl/Device` ▸ |  |  |  |
|  | `/ISAPI/AccessControl/Device/HardWare` ▸ |  |  |  |
| GET | `/ISAPI/AccessControl/Device/HardWare/capabilities` | `0x1e4508` |  |  |
| GET+PUT | `/ISAPI/AccessControl/Card` |  |  |  |
| GET+PUT | `/ISAPI/AccessControl/Card/sectorCheck` | `0x1e31c4` | `isapiAcsCtrlCardSectorCheckBasic` |  |
| GET | `/ISAPI/AccessControl/Card/sectorCheck/capabilities` | `0x1e37d4` | `isapiAcsCtrlCardSectorCheckCapBasic` |  |
|  | `/ISAPI/AccessControl/Door` ▸ |  |  |  |
| GET | `/ISAPI/AccessControl/Door/param` | `0x1e39ac` | `isapiAcsCtrlDoorParamBasic` | no RemoteControl/door — see door-unlock-api.md |
|  | `/ISAPI/AccessControl/Door/param/index` |  |  |  |

## /ISAPI/Streaming

| Method | Path | handler | name (rewrite) | notes |
|---|---|---|---|---|
| GET | `/ISAPI/Streaming/index` |  |  |  |
| GET+PUT | `/ISAPI/Streaming/channels` | `0x1daea4` |  | RTSP channel cfg (ch1/av_stream) |
| GET | `/ISAPI/Streaming/channels/index` |  |  |  |

## /ISAPI/SecurityCP

| Method | Path | handler | name (rewrite) | notes |
|---|---|---|---|---|
| GET | `/ISAPI/SecurityCP/capabilities` | `0x1e01f0` |  | whole SecurityCP tree requires `g_hwreg+0x2b8` bit7 |
| GET | `/ISAPI/SecurityCP/BasicParam` |  |  |  |
| POST/DEL†+PUT | `/ISAPI/SecurityCP/BasicParam/ZoneAssociatedDetectorCfg` | `0x1e23b0` | `isapiSecurityCPZoneAssociatedDetectorCfgBasic` |  |
| GET | `/ISAPI/SecurityCP/BasicParam/ZoneAssociatedDetectorCfg/capabilities` | `0x1e2b50` |  |  |
|  | `/ISAPI/SecurityCP/BasicParam/zones` ▸ |  |  |  |
|  | `/ISAPI/SecurityCP/BasicParam/zones/index` |  |  |  |
| GET+PUT | `/ISAPI/SecurityCP/BasicParam/sceneZoneCfg` | `0x1e14cc` | `isapiSecurityCPSceneZoneCfgBasic` |  |
| GET | `/ISAPI/SecurityCP/BasicParam/sceneZoneCfg/capabilities` | `0x1e1b28` | `isapiSecurityCPSceneZoneCfgCapBasic` |  |
| GET+POST/DEL†+PUT ⁺ | `/ISAPI/SecurityCP/BasicParam/zoneConfigList` | `0x1e1d10` | `isapiSecurityCPzoneConfigListBasic` |  |
| GET | `/ISAPI/SecurityCP/BasicParam/zoneConfigList/capabilities` | `0x1e1f10` | `isapiSecurityCPzoneConfigListCapBasic` |  |
| GET+PUT | `/ISAPI/SecurityCP/AlarmControlByPhone` | `0x1e0e08` | `isapiSecurityCPAlarmCtrlByPhoneBasic` |  |
| GET | `/ISAPI/SecurityCP/AlarmControlByPhone/capabilities` | `0x1e12fc` | `isapiSecurityCPAlarmCtrlByPhoneCapBasic` |  |

## /ISAPI/ContentMgmt

| Method | Path | handler | name (rewrite) | notes |
|---|---|---|---|---|
| GET+POST/DEL†+PUT ⁺ | `/ISAPI/ContentMgmt/logSearch` | `0x1c7470` |  | const-stub → `0xd` — **registered but disabled** in this build |

## /ISAPI/SmartLock

| Method | Path | handler | name (rewrite) | notes |
|---|---|---|---|---|
| GET | `/ISAPI/SmartLock/DetectorMatched` | `0x2023c8` | `isapiIntercomDetectMatchedBasic` |  |
| GET | `/ISAPI/SmartLock/DetectorMatched/capabilities` | `0x2027b4` |  |  |
|  | `/ISAPI/SmartLock/Detector` ▸ |  |  |  |
|  | `/ISAPI/SmartLock/Detector/AddDetector` ▸ |  |  |  |
| GET | `/ISAPI/SmartLock/Detector/AddDetector/capabilities` | `0x203174` |  |  |
| GET | `/ISAPI/SmartLock/DelUserInfo` |  |  |  |
| GET | `/ISAPI/SmartLock/DelUserInfo/capabilities` | `0x1ff3e4` |  |  |
| GET | `/ISAPI/SmartLock/capabilities` | `0x1fb96c` | `isapiSmartLockUserInfoExtBasic` |  |
|  | `/ISAPI/SmartLock/index` |  |  |  |

## /ISAPI/SingleSmartLock

| Method | Path | handler | name (rewrite) | notes |
|---|---|---|---|---|
|  | `/ISAPI/SingleSmartLock/index` |  |  |  |

## Caveats / unknowns

- **Method semantics** live inside each dispatcher (`param_2[2]`:
  1=GET, 4=PUT confirmed); the `†` bit1 verb (POST or DELETE) isn't
  rewritten yet — the flag word is the authoritative allowed-method
  hint per route.
- `▸` = directory nodes; `index` = `/<id>` wildcards, all with
  handler `0` in this build — ids resolve through the parent dispatcher.
- `/ISAPI/ContentMgmt/logSearch` registers a 2-instruction stub
  (`mov r0,#0xd; bx lr`) — route exists, functionally disabled.
- `/ISAPI/Image/channels` self-nests (`…/channels/channels`) — genuine
  table self-reference; image endpoints are empty on this headless unit.
- `SmartLock`/`SingleSmartLock`: mostly directory+capabilities stubs —
  wireless-lock actions largely absent in this build.
- Two `capabilities` rows sit in the `SystemSwitchCfg` child block —
  the table literally contains both (first match wins at dispatch).
- The flag bit on handler-less dir rows (`Linkage`, `Streaming/index`,
  `BasicParam`… all `GET+PUT`/`GET`) describes subtree verbs, not a
  callable handler.

