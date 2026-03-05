import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css' // 必须引入样式，否则组件没有外观
import './style.css'
import App from './App.vue'

const app = createApp(App)

app.use(ElementPlus)
app.mount('#app')