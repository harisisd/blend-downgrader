const { contextBridge, ipcRenderer, webUtils } = require("electron");

const invoke = (ch) => (...a) => ipcRenderer.invoke(ch, ...a);

contextBridge.exposeInMainWorld("api", {
  listBlenders: invoke("blenders:list"),
  addBlender: invoke("blenders:add"),
  removeBlender: invoke("blenders:remove"),
  series: invoke("blenders:series"),
  resolveRelease: invoke("blenders:resolve"),
  installBlender: invoke("blenders:install"),
  pickFiles: invoke("files:pick"),
  probe: invoke("files:probe"),
  pickFolder: invoke("folder:pick"),
  convert: invoke("convert:one"),
  cancel: invoke("cancel"),
  getSettings: invoke("settings:get"),
  setSettings: invoke("settings:set"),
  reveal: invoke("reveal"),
  openIn: invoke("open-in"),
  info: invoke("app:info"),
  pathFor: (file) => webUtils.getPathForFile(file),
  on: (ch, fn) => {
    const h = (_e, d) => fn(d);
    ipcRenderer.on(ch, h);
    return () => ipcRenderer.removeListener(ch, h);
  },
});
