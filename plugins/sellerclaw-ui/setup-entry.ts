import { defineSetupPluginEntry } from "openclaw/plugin-sdk/core";

import { sellerclawUiChannelPlugin } from "./src/channel.js";

export default defineSetupPluginEntry(sellerclawUiChannelPlugin);
