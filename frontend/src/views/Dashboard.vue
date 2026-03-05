<template>
  <div class="screen-container">
    
    <div class="dashboard-wrapper" :style="wrapperStyle">
      
      <header class="cscec-header">
        <div class="header-left">
          <div class="logo">CSCEC</div>
          <h1 class="sys-title">施工现场智能进度数字监控舱</h1>
        </div>
        <div class="header-right">
          <el-button type="danger" plain size="large" @click="confirmReset">
            <el-icon><Delete /></el-icon> 重置系统
          </el-button>
          
          <el-button type="primary" plain size="large" @click="manualFixVisible = true">
            <el-icon><Edit /></el-icon> 手动修正进度
          </el-button>
          <el-button type="primary" plain size="large" @click="settingVisible = true">
            <el-icon><Setting /></el-icon> 系统配置
          </el-button>
        </div>
      </header>

      <main class="main-content">
        <el-row :gutter="24" class="full-height">
          
          <el-col :span="14" class="col-flex">
            <el-card shadow="hover" class="video-card flex-item" :body-style="{ padding: '15px', display: 'flex', flexDirection: 'column', flex: 1, overflow: 'hidden' }">
              <template #header>
                <div class="card-header">
                  <span class="card-title">📷 现场实时视觉抓拍</span>
                  <el-tag effect="light" type="primary" size="large">当前绑定区域: {{ config.current_zone || '未配置' }}</el-tag>
                </div>
              </template>
              <div class="video-wrapper">
                <el-image v-if="snapshotUrl" :src="snapshotUrl" fit="contain" class="snapshot-img"></el-image>
                <el-empty v-else description="暂无现场画面"></el-empty>
              </div>
              <div class="action-bar">
                <el-button type="primary" size="large" class="analyze-btn" :loading="isCapturing" @click="triggerManualCapture">
                  📸 抓拍并运行 Qwen-VL-Max 分析
                </el-button>
              </div>
            </el-card>

            <el-card shadow="hover" class="log-card flex-item-fixed" :body-style="{ padding: '15px' }">
              <template #header>
                <div class="card-header"><span class="card-title">🧠 大模型推理记录与分析结果</span></div>
              </template>
              <el-row :gutter="20">
                <el-col :span="12">
                  <div class="light-terminal" ref="terminalRef">
                    <div v-for="(log, idx) in llmLogs" :key="idx" :class="['log-text', log.type]">
                      <span class="log-time">[{{ log.time }}]</span> {{ log.msg }}
                    </div>
                    <div v-if="isCapturing" class="log-text warning blinking">
                      <span class="log-time">[{{ new Date().toLocaleTimeString() }}]</span> 正在提取多模态特征...
                    </div>
                  </div>
                </el-col>
                <el-col :span="12">
                  <div class="json-result-box">
                    <div class="json-title">JSON 解析结果:</div>
                    <pre v-if="llmRawResult" class="json-content">{{ JSON.stringify(llmRawResult, null, 2) }}</pre>
                    <div v-else class="json-empty">等待分析指令...</div>
                  </div>
                </el-col>
              </el-row>
            </el-card>
          </el-col>

          <el-col :span="10" class="col-flex">
            <el-card shadow="hover" class="progress-card flex-item-fixed">
              <template #header>
                <div class="card-header"><span class="card-title">📊 楼栋总体施工进度</span></div>
              </template>
              <div class="progress-list">
                <div v-for="(item, idx) in progressData" :key="idx" class="progress-item">
                  <div class="p-header">
                    <span class="p-zone">{{ item.zone_name }}</span>
                    <span class="p-floor">当前: <b>{{ item.floor }}层</b> ({{ item.stage }})</span>
                  </div>
                  <el-progress 
                    :percentage="getOverallProgress(item.floor)" 
                    :stroke-width="18"
                    :text-inside="true"
                    status="success" />
                </div>
              </div>
            </el-card>

            <el-card shadow="hover" class="timeline-card flex-item" :body-style="{ padding: '0', display: 'flex', flexDirection: 'column', flex: 1, overflow: 'hidden' }">
              <template #header>
                <div class="card-header">
                  <span class="card-title">🔍 工序台账时间轴</span>
                  <el-select v-model="selectedZone" size="default" style="width: 150px" @change="fetchTimeline">
                    <el-option v-for="item in progressData" :key="item.zone_name" :label="item.zone_name" :value="item.zone_name" />
                  </el-select>
                </div>
              </template>
              <el-table 
                :data="timelineData" 
                :span-method="mergeFloorCells"
                height="100%" 
                stripe 
                border
                size="default"
                class="custom-table">
                <el-table-column prop="floor" label="施工层" width="90" align="center">
                  <template #default="scope"><span class="floor-text">{{ scope.row.floor }}F</span></template>
                </el-table-column>
                <el-table-column prop="stage" label="当前工序" width="120" align="center">
                  <template #default="scope">
                    <el-tag :type="getStageTagType(scope.row.stage)" size="default">{{ scope.row.stage }}</el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="start_time" label="开始时间" align="center" min-width="160"></el-table-column>
                <el-table-column prop="end_time" label="结束时间" align="center" min-width="160">
                  <template #default="scope">
                    <span v-if="scope.row.end_time" class="time-ended">{{ scope.row.end_time }}</span>
                    <span v-else class="time-active">进行中...</span>
                  </template>
                </el-table-column>
              </el-table>
            </el-card>
          </el-col>
        </el-row>
      </main>

    </div>

    <el-dialog v-model="settingVisible" title="⚙️ 监控舱系统配置" width="500px">
      <el-form :model="configForm" label-position="top">
        <el-form-item label="RTSP 视频流地址">
          <el-input v-model="configForm.rtsp_url"></el-input>
        </el-form-item>
        <el-form-item label="当前绑定的施工区域">
          <el-input v-model="configForm.current_zone"></el-input>
        </el-form-item>
        <el-form-item label="自动抓拍间隔 (分钟, 0为关闭)">
          <el-input-number v-model="configForm.auto_interval_minutes" :min="0" style="width: 100%"></el-input-number>
        </el-form-item>
        <el-form-item label="全局楼层序列 (英文逗号分隔)">
          <el-input v-model="configForm.floors_str" type="textarea" :rows="3"></el-input>
          <div class="help-text">用于计算总进度百分比，例如: B2,B1,1,2,3,4,5,屋面层</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="settingVisible = false">取消</el-button>
        <el-button type="primary" @click="saveConfig">保存配置</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="manualFixVisible" title="🔧 手动进度修正" width="400px">
      <el-form :model="manualForm" label-width="80px">
        <el-form-item label="施工区域">
          <el-select v-model="manualForm.zone_name" style="width: 100%">
            <el-option v-for="item in progressData" :key="item.zone_name" :label="item.zone_name" :value="item.zone_name" />
          </el-select>
        </el-form-item>
        <el-form-item label="目标楼层">
          <el-input v-model="manualForm.target_floor"></el-input>
        </el-form-item>
        <el-form-item label="目标工序">
          <el-select v-model="manualForm.target_stage" style="width: 100%">
            <el-option label="模板阶段" value="模板阶段" />
            <el-option label="钢筋阶段" value="钢筋阶段" />
            <el-option label="混凝土阶段" value="混凝土阶段" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="manualFixVisible = false">取消</el-button>
        <el-button type="primary" @click="submitManualFix">确认修正</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Setting, Edit, Delete } from '@element-plus/icons-vue'
import axios from 'axios'

const apiBase = 'http://localhost:8000/api'

// ==== 核心：硬核物理绝对居中计算引擎 ====
const scale = ref(1)
const offsetX = ref(0)
const offsetY = ref(0)

const updateScale = () => {
  // 获取当前浏览器可视区域真实的宽和高
  const ww = window.innerWidth
  const wh = window.innerHeight
  // 基于 1920x1080 计算出可以完整塞入屏幕的极限缩放比例
  const s = Math.min(ww / 1920, wh / 1080)
  scale.value = s
  // 精确计算物理居中所需的 Left 和 Top 偏移量
  offsetX.value = (ww - 1920 * s) / 2
  offsetY.value = (wh - 1080 * s) / 2
}

// 通过绝对定位彻底绑定画布位置
const wrapperStyle = computed(() => ({
  transform: `scale(${scale.value})`,
  left: `${offsetX.value}px`,
  top: `${offsetY.value}px`
}))
// ==================================

const config = ref({ floors: [] })
const progressData = ref([])
const timelineData = ref([])
const selectedZone = ref('')
const snapshotUrl = ref('')
const isCapturing = ref(false)

const llmLogs = ref([]) 
const llmRawResult = ref(null)
const terminalRef = ref(null)

const settingVisible = ref(false)
const manualFixVisible = ref(false)
const configForm = ref({ rtsp_url: '', current_zone: '', auto_interval_minutes: 0, floors_str: '' })
const manualForm = ref({ zone_name: '', target_floor: '', target_stage: '模板阶段' })

const addLog = (msg, type = 'info') => {
  const time = new Date().toLocaleTimeString('en-GB')
  llmLogs.value.push({ time, msg, type })
  if (llmLogs.value.length > 30) llmLogs.value.shift()
  nextTick(() => { if (terminalRef.value) terminalRef.value.scrollTop = terminalRef.value.scrollHeight })
}

const fetchConfig = async () => {
  const res = await axios.get(`${apiBase}/config`)
  config.value = res.data
  configForm.value = { ...res.data, floors_str: (res.data.floors || []).join(',') }
}

const saveConfig = async () => {
  try {
    const payload = { ...configForm.value, floors: configForm.value.floors_str.split(',').map(s => s.trim()).filter(Boolean) }
    const res = await axios.post(`${apiBase}/config`, payload)
    ElMessage.success(res.data.message)
    settingVisible.value = false
    fetchConfig()
  } catch (e) { ElMessage.error("保存失败") }
}

const fetchProgress = async () => {
  const res = await axios.get(`${apiBase}/progress`)
  progressData.value = res.data.data || []
  if(progressData.value.length > 0 && !selectedZone.value) {
    selectedZone.value = progressData.value[0].zone_name
    fetchTimeline()
  }
}

const fetchTimeline = async () => {
  if (!selectedZone.value) return
  const res = await axios.get(`${apiBase}/timeline/${selectedZone.value}`)
  timelineData.value = res.data.data || []
}

const mergeFloorCells = ({ row, column, rowIndex, columnIndex }) => {
  if (columnIndex === 0) { 
    if (rowIndex === 0 || timelineData.value[rowIndex - 1].floor !== row.floor) {
      let rowspan = 1;
      for (let i = rowIndex + 1; i < timelineData.value.length; i++) {
        if (timelineData.value[i].floor === row.floor) rowspan++;
        else break;
      }
      return { rowspan, colspan: 1 };
    } else {
      return { rowspan: 0, colspan: 0 };
    }
  }
}

const getOverallProgress = (currentFloor) => {
  const floors = config.value.floors || []
  if (floors.length === 0) return 0
  const idx = floors.indexOf(currentFloor)
  if (idx === -1) return 0
  return Math.round(((idx + 1) / floors.length) * 100)
}

const triggerManualCapture = async () => {
  isCapturing.value = true
  llmRawResult.value = null 
  addLog("发起视觉抓拍与 AI 推理请求...", "info")
  
  try {
    const res = await axios.post(`${apiBase}/capture/manual`)
    addLog(`识别完成: ${res.data.message}`, "success")
    llmRawResult.value = res.data.llm_result 
    
    ElMessage.success("智能识别完成")
    snapshotUrl.value = `${apiBase}/snapshot/latest?t=${new Date().getTime()}`
    fetchProgress()
    if (selectedZone.value) fetchTimeline()
  } catch (error) {
    const errMsg = error.response?.data?.detail || '识别失败'
    addLog(`异常: ${errMsg}`, "error")
    ElMessage.error(errMsg)
  } finally {
    isCapturing.value = false
  }
}

const submitManualFix = async () => {
  try {
    await axios.post(`${apiBase}/progress/manual`, manualForm.value)
    ElMessage.success("手动修正成功")
    manualFixVisible.value = false
    fetchProgress()
    fetchTimeline()
  } catch (e) { ElMessage.error("更新失败") }
}

const confirmReset = () => {
  ElMessageBox.confirm('此操作将重置系统到初始状态，是否继续？', '⚠️ 系统重置', {
    confirmButtonText: '确认重置',
    cancelButtonText: '取消',
    type: 'warning',
  }).then(async () => {
    try {
      await axios.post(`${apiBase}/project/reset`)
      ElMessage.success("系统已重置")
      fetchConfig()
      fetchProgress()
      llmLogs.value = []
      llmRawResult.value = null
      snapshotUrl.value = ''
    } catch (e) { ElMessage.error("重置失败") }
  }).catch(() => { /* 用户取消，无需操作 */ })
}

const getStageTagType = (stage) => {
  if (stage === '混凝土阶段') return 'success';
  if (stage === '钢筋阶段') return 'warning';
  return 'info';
}

onMounted(() => {
  updateScale()
  window.addEventListener('resize', updateScale)

  fetchConfig()
  fetchProgress()
  snapshotUrl.value = `${apiBase}/snapshot/latest?t=${new Date().getTime()}`
  addLog("系统初始化成功，大模型引擎就绪。", "info")
  setInterval(() => { fetchProgress() }, 60000)
})

onUnmounted(() => {
  window.removeEventListener('resize', updateScale)
})
</script>

<style scoped>
/* ========================================================= */
/* 大屏缩放底层容器：强行霸占视口，无视浏览器的margin */
/* ========================================================= */
.screen-container {
  position: fixed; /* 使用 fixed 定位，一劳永逸干掉滚动条和留白 */
  top: 0;
  left: 0;
  width: 100vw;
  height: 100vh;
  background-color: #1a1a1a;
  overflow: hidden; 
  z-index: 1;
}

/* 核心画布：死死钉死在 1920 x 1080 像素！ */
.dashboard-wrapper {
  position: absolute; /* 使用绝对定位，不依靠 Flex 的居中 */
  width: 1920px;
  height: 1080px;
  background-color: #f2f5f8;
  display: flex;
  flex-direction: column;
  box-shadow: 0 0 50px rgba(0,0,0,0.8);
  font-family: "Helvetica Neue", Helvetica, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", Arial, sans-serif;
  transform-origin: 0 0 !important; /* 极其重要：把缩放原点钉在左上角(0,0)位置 */
}
/* ========================================================= */

.cscec-header {
  height: 80px;
  background-color: #004b87;
  color: #fff;
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0 30px;
  flex-shrink: 0;
}
.header-left, .header-right { display: flex; align-items: center; gap: 20px; }
.logo { font-size: 26px; font-weight: 900; background: #fff; color: #004b87; padding: 4px 12px; border-radius: 6px; }
.sys-title { font-size: 24px; font-weight: 500; margin: 0; letter-spacing: 2px; }

.main-content {
  height: calc(1080px - 80px); 
  padding: 20px;
  box-sizing: border-box;
}
.full-height { height: 100%; margin: 0 !important; }
.col-flex { display: flex; flex-direction: column; height: 100%; gap: 20px; padding: 0 10px !important;}
.flex-item { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
.flex-item-fixed { flex-shrink: 0; }

:deep(.el-card) { border-radius: 8px; border: none; box-shadow: 0 2px 12px rgba(0,0,0,0.05); }
:deep(.el-card__header) { padding: 15px 20px; background-color: #fff; border-bottom: 1px solid #ebeef5; }
.card-header { display: flex; justify-content: space-between; align-items: center; }
.card-title { font-size: 18px; font-weight: bold; color: #303133; }

.video-wrapper { flex: 1; background: #eaedf1; border-radius: 6px; overflow: hidden; position: relative; margin-bottom: 15px; }
.snapshot-img { position: absolute; width: 100%; height: 100%; top: 0; left: 0; }
.action-bar { text-align: center; flex-shrink: 0; }
.analyze-btn { width: 100%; font-size: 18px; height: 50px; font-weight: bold; letter-spacing: 2px; }

.log-card { height: 320px; }
.light-terminal {
  height: 220px;
  background: #f8f9fa; border: 1px solid #e4e7ed; border-radius: 6px;
  padding: 12px; overflow-y: auto; font-family: 'Consolas', monospace; font-size: 14px; line-height: 1.6;
}
.log-text { color: #606266; margin-bottom: 4px; }
.log-time { color: #909399; }
.log-text.info { color: #303133; }
.log-text.success { color: #67c23a; }
.log-text.warning { color: #e6a23c; }
.log-text.error { color: #f56c6c; }

.json-result-box { height: 220px; display: flex; flex-direction: column; }
.json-title { font-size: 14px; color: #909399; margin-bottom: 6px; font-weight: bold;}
.json-content {
  flex: 1; margin: 0; padding: 12px; background: #eff2f6; border-radius: 6px;
  font-family: 'Consolas', monospace; font-size: 14px; color: #004b87;
  overflow: auto; border: 1px dashed #dcdfe6; line-height: 1.5;
}
.json-empty { flex: 1; display: flex; align-items: center; justify-content: center; color: #c0c4cc; font-size: 14px; background: #fafafa; border-radius: 6px; border: 1px dashed #e4e7ed;}

.progress-list { padding: 10px; max-height: 200px; overflow-y: auto;}
.progress-item { margin-bottom: 15px; }
.progress-item:last-child { margin-bottom: 0; }
.p-header { display: flex; justify-content: space-between; margin-bottom: 8px; font-size: 16px; }
.p-zone { color: #606266; font-weight: bold; }
.p-floor { color: #303133; }
.p-floor b { color: #004b87; font-size: 20px; }

.custom-table { width: 100%; font-size: 15px; }
.floor-text { font-weight: bold; color: #004b87; font-size: 16px; }
.time-ended { color: #606266; }
.time-active { color: #e6a23c; font-weight: bold; }

.blinking { animation: blinker 1.5s linear infinite; }
@keyframes blinker { 50% { opacity: 0.4; } }
.help-text { font-size: 13px; color: #909399; margin-top: 6px; }
</style>