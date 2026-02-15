# ThermIQ-MQTT Messaging (Version >2.40)

When connecting ThermIQ will send a Topic named announce.

ThermIQ will regularly send to a MQTT topic named `ThermIQ/ThermIQ-room2` with the data read from the Heatpump.

Note that register can be either hex (`rXX`) or decimal (`dDD`) - any parsing must handle both formats.

## Command MQTT Interface

Commands are published as JSON messages to topic `ThermIQ/ThermIQ-room2/<command>>`.

| Command  | JSON Payload                       | Description                                                                 |
|----------|------------------------------------|-----------------------------------------------------------------------------|
| `read`   |                                    | Read all registers                                                          |
| `write`  | `{"r05":10,"r3f":22}`              | Write to register `r` with address in hex and data in decimal               |
| `write`  | `{"d5":10,"d63":22,"d11":5}`       | Write to register `d` with address in decimal and data in decimal           |
| `set`    | `{"INDR_T":20.3}`                  | Set actual indoor temp (only with Room sensor option: ThermIQ-Room/Room2)    |
| `set`    | `{"EVU":1}`                        | Set EVU to 0 or 1 (only with ThermIQ-Room2)                                |
| `set`    | `{"REGFMT":1}`                     | Change register notation from hex to decimal (e.g. `r20` -> `d32`). Set to 0 for hex |
| `info`   |                                    | Get node info including heap and uptime                                     |
| `update` |                                    | Attempt firmware update                                                     |
| `reset`  |                                    | Reset device                                                                |

## Thermia Control Registers (Read Only)

| Reg (Dec) | Reg (Hex) | Content                          | Type    |
|-----------|-----------|----------------------------------|---------|
| 0         | r00       | Outdoor temp.                    | C       |
| 1         | r01       | Indoor temp.                     | C       |
| 2         | r02       | Indoor temp., decimal            | 0.1C    |
| 3         | r03       | Indoor target temp.              | C       |
| 4         | r04       | Indoor target temp., decimal     | 0.1C    |
| 5         | r05       | Supplyline temp.                 | C       |
| 6         | r06       | Returnline temp.                 | C       |
| 7         | r07       | Hotwater temp.                   | C       |
| 8         | r08       | Brine out temp.                  | C       |
| 9         | r09       | Brine in temp.                   | C       |
| 10        | r0a       | Cooling temp.                    | C       |
| 11        | r0b       | Supplyline temp., shunt          | C       |
| 12        | r0c       | Electrical current               | A       |
| 13        | r0d       | *(bitfield, see below)*          |         |
| 13:0      | r0d:0     | Aux. heater 3 kW                 | Boolean |
| 13:1      | r0d:1     | Aux. heater 6 kW                 | Boolean |
| 14        | r0e       | Supplyline target temp.          | C       |
| 15        | r0f       | Supplyline target temp., shunt   | C       |
| 16        | r10       | *(bitfield, see below)*          |         |
| 16:0      | r10:0     | Brinepump                        | Boolean |
| 16:1      | r10:1     | Compressor                       | Boolean |
| 16:2      | r10:2     | Flowlinepump                     | Boolean |
| 16:3      | r10:3     | Hotwater production              | Boolean |
| 16:4      | r10:4     | Auxiliary 2                      | Boolean |
| 16:5      | r10:5     | Shunt -                          | Boolean |
| 16:6      | r10:6     | Shunt +                          | Boolean |
| 16:7      | r10:7     | Auxiliary 1                      | Boolean |
| 17        | r11       | *(bitfield, see below)*          |         |
| 17:0      | r11:0     | Shuntgroup -                     | Boolean |
| 17:1      | r11:1     | Shuntgroup +                     | Boolean |
| 17:2      | r11:2     | Shunt cooling -                  | Boolean |
| 17:3      | r11:3     | Shunt cooling +                  | Boolean |
| 17:4      | r11:4     | Active cooling                   | Boolean |
| 17:5      | r11:5     | Passive cooling                  | Boolean |
| 17:6      | r11:6     | Alarm                            | Boolean |
| 18        | r12       | PWM Out                          | Units   |
| 19        | r13       | *(alarm bitfield, see below)*    |         |
| 19:0      | r13:0     | Alarm highpr. pressostate        | Boolean |
| 19:1      | r13:1     | Alarm lowpr. pressostate         | Boolean |
| 19:2      | r13:2     | Alarm motor circuit breaker      | Boolean |
| 19:3      | r13:3     | Alarm low flow brine             | Boolean |
| 19:4      | r13:4     | Alarm low temp. brine            | Boolean |
| 20        | r14       | *(alarm bitfield, see below)*    |         |
| 20:0      | r14:0     | Alarm outdoor t-sensor           | Boolean |
| 20:1      | r14:1     | Alarm supplyline t-sensor        | Boolean |
| 20:2      | r14:2     | Alarm returnline t-sensor        | Boolean |
| 20:3      | r14:3     | Alarm hotw. t-sensor             | Boolean |
| 20:4      | r14:4     | Alarm indoor t-sensor            | Boolean |
| 20:5      | r14:5     | Alarm incorrect 3-phase order    | Boolean |
| 20:6      | r14:6     | Alarm overheating                | Boolean |
| 21        | r15       | DEMAND1                          |         |
| 22        | r16       | DEMAND2                          |         |
| 23        | r17       | Pressurepipe temp.               | C       |
| 24        | r18       | Hotw. supplyline temp.           | C       |
| 25        | r19       | Integral                         | C*min   |
| 26        | r1a       | Integral, reached                | A-limit |
| 27        | r1b       | Defrost                          | *10s    |
| 28        | r1c       | Minimum time to start            | min     |
| 29        | r1d       | Program version                  |         |
| 30        | r1e       | Flowlinepump speed               | %       |
| 31        | r1f       | Brinepump speed                  | %       |
| 32        | r20       | STATUS3                          |         |

## Thermia Control Registers (Read / Write)

| Reg (Dec) | Reg (Hex) | Content                                | Type    |
|-----------|-----------|----------------------------------------|---------|
| 50        | r32       | Indoor target temp.                    | C       |
| 51        | r33       | Mode                                   | #       |
| 52        | r34       | Curve                                  | *       |
| 53        | r35       | Curve min                              | *       |
| 54        | r36       | Curve max                              | *       |
| 55        | r37       | Curve +5                               | *       |
| 56        | r38       | Curve 0                                | *       |
| 57        | r39       | Curve -5                               | *       |
| 58        | r3a       | Heatstop                               | C       |
| 59        | r3b       | Temp. reduction                        | C       |
| 60        | r3c       | Room factor                            | *       |
| 61        | r3d       | Curve 2                                | *       |
| 62        | r3e       | Curve 2 min                            | *       |
| 63        | r3f       | Curve 2 max                            | *       |
| 64        | r40       | Curve 2, Target                        | C       |
| 65        | r41       | Curve 2, Actual                        | C       |
| 66        | r42       | Outdoor stop temp. (20 = -20C)         | *       |
| 67        | r43       | Pressurepipe, temp. limit              | C       |
| 68        | r44       | Hotwater start temp.                   | C       |
| 69        | r45       | Hotwater operating time                | min     |
| 70        | r46       | Heatpump operating time                | min     |
| 71        | r47       | Legionella interval                    | days    |
| 72        | r48       | Legionella stop temp.                  | C       |
| 73        | r49       | Integral limit A1                      | C*min   |
| 74        | r4a       | Hysteresis, heatpump                   | C       |
| 75        | r4b       | Returnline temp., max limit            | C       |
| 76        | r4c       | Minimum starting interval              | min     |
| 77        | r4d       | Brine temp., min limit (-15 = OFF)     | C       |
| 78        | r4e       | Cooling, target                        | C       |
| 79        | r4f       | Integral limit A2                      | 10C*min |
| 80        | r50       | Hysteresis limit, aux                  | C       |
| 81        | r51       | Max step, aux                          | # steps |
| 82        | r52       | Electrical current, max limit          | A       |
| 83        | r53       | Shunt time                             | s       |
| 84        | r54       | Hotwater stop temp.                    | C       |
| 85        | r55       | Manual test mode                       | mode #  |
| 86        | r56       | DT_LARMOFF                             |         |
| 87        | r57       | Language                               | lang #  |
| 88        | r58       | SERVFAS                                |         |
| 89        | r59       | Factory settings                       | setting # |
| 90        | r5a       | Reset runtime counters                 | C       |
| 91        | r5b       | Calibration outdoor sensor             |         |
| 92        | r5c       | Calibration supplyline sensor          |         |
| 93        | r5d       | Calibration returnline sensor          |         |
| 94        | r5e       | Calibration hotwater sensor            |         |
| 95        | r5f       | Calibration brine out sensor           |         |
| 96        | r60       | Calibration brine in sensor            |         |
| 97        | r61       | Heating system type (0=VL, 4=D)        | type #  |
| 98        | r62       | *(bitfield, accessed as integer only)* |         |
| 98:0      | r62:0     | Add-on phase order measurement         | Boolean |
| 98:1      | r62:1     | TILL2                                  | Boolean |
| 98:2      | r62:2     | Add-on HGW                             | Boolean |
| 98:3      | r62:3     | TILL4                                  | Boolean |
| 98:4      | r62:4     | TILL5                                  | Boolean |
| 98:5      | r62:5     | TILL6                                  | Boolean |
| 98:6      | r62:6     | Add-on Optimum                         | Boolean |
| 98:7      | r62:7     | Add-on flow guard                      | Boolean |
| 99        | r63       | Logging time                           | min     |
| 100       | r64       | Brine run-out duration                 | *10s    |
| 101       | r65       | Brine run-in duration                  | *10s    |
| 102       | r66       | Legionella peak heating enable         | Boolean |
| 103       | r67       | Legionella peak heating duration       | h       |
| 104       | r68       | Runtime compressor                     | h       |
| 105       | r69       | DVP_MSD1                               |         |
| 106       | r6a       | Runtime 3 kW                           | h       |
| 107       | r6b       | DTS_MSD1                               |         |
| 108       | r6c       | Runtime hotwater production            | h       |
| 109       | r6d       | DVV_MSD1                               |         |
| 110       | r6e       | Runtime passive cooling                | h       |
| 111       | r6f       | DPAS_MSD1                              |         |
| 112       | r70       | Runtime active cooling                 | h       |
| 113       | r71       | DACT_MSD1                              |         |
| 114       | r72       | Runtime 6 kW                           | h       |
| 115       | r73       | DTS2_MSD1                              |         |
| 116       | r74       | GrafCounterOffset                      |         |
| 117-127   | r75-r7f   | Unknown / Undocumented                 |         |
