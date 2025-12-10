import qt
import slicer
import vtk
import math

# ---------------------------------------------------------
# 기존 윈도우/액터 정리
# ---------------------------------------------------------
if hasattr(slicer, 'tagControlWindow'):
    try:
        slicer.tagControlWindow.close()
    except:
        pass

if hasattr(slicer, 'tagActors'):
    view = slicer.app.layoutManager().threeDWidget(0).threeDView()
    renderer = view.renderWindow().GetRenderers().GetFirstRenderer()
    for actor in slicer.tagActors:
        try:
            renderer.RemoveActor(actor)
        except:
            pass
    view.forceRender()

# ---------------------------------------------------------
# 전역 상태 초기화
# ---------------------------------------------------------
slicer.tagActors = []              # text + line actor 전체 리스트
slicer.tagVisible = False
slicer.tagSize = 5
slicer.tagActorsBySegment = {}     # { segmentId: [textActor, lineActor] }
slicer.tagDisplayObserverTag = None

# 텍스트 색 설정 (True이면 세그먼트 색, False이면 고정 색)
USE_SEGMENT_COLOR = False
FIXED_TEXT_COLOR = (1.0, 1.0, 1.0)  # 흰색


# ---------------------------------------------------------
# 유틸 함수들
# ---------------------------------------------------------
def _getThreeDViewAndRenderer():
    """첫 번째 3D View와 Renderer를 반환 (없으면 None, None)."""
    lm = slicer.app.layoutManager()
    if not lm or lm.threeDViewCount == 0:
        print("❌ 3D View가 없습니다. 3D 레이아웃에서 실행해주세요.")
        return None, None
    view = lm.threeDWidget(0).threeDView()
    renderer = view.renderWindow().GetRenderers().GetFirstRenderer()
    return view, renderer


def _updateActorsVisibilityFromSegmentation():
    """세그멘테이션 display node의 segment visibility에 따라 태그 visibility 갱신."""
    seg_node = slicer.mrmlScene.GetFirstNodeByClass('vtkMRMLSegmentationNode')
    if not seg_node:
        return
    displayNode = seg_node.GetDisplayNode()
    if not displayNode:
        return

    globalVisible = slicer.tagVisible
    for segmentId, actors in slicer.tagActorsBySegment.items():
        # 세그먼트 eye 상태
        segVisible = bool(displayNode.GetSegmentVisibility(segmentId))
        for actor in actors:
            actor.SetVisibility(globalVisible and segVisible)

    view, _ = _getThreeDViewAndRenderer()
    if view:
        view.forceRender()


def _onSegmentationDisplayModified(caller, event):
    """세그멘테이션 display node가 바뀔 때마다 호출."""
    _updateActorsVisibilityFromSegmentation()


# ---------------------------------------------------------
# 메인 기능들
# ---------------------------------------------------------
def createTags(size=5):
    """세그먼트 이름을 3D에 태그로 표시."""
    # 기존 태그 제거
    if slicer.tagActors:
        view, renderer = _getThreeDViewAndRenderer()
        if not view:
            return False
        for actor in slicer.tagActors:
            try:
                renderer.RemoveActor(actor)
            except:
                pass
        slicer.tagActors = []
        slicer.tagActorsBySegment = {}
        view.forceRender()

    view, renderer = _getThreeDViewAndRenderer()
    if not view:
        return False

    # 세그멘테이션 노드
    seg_node = slicer.mrmlScene.GetFirstNodeByClass('vtkMRMLSegmentationNode')
    if not seg_node:
        print("❌ 세그멘테이션 노드 없음")
        return False

    segmentation = seg_node.GetSegmentation()
    if segmentation.GetNumberOfSegments() == 0:
        print("❌ 세그먼트 없음")
        return False

    # 🔹 전체 세그멘테이션 bounds로 global center 계산
    boundsAll = [0.0] * 6
    seg_node.GetBounds(boundsAll)
    if boundsAll[0] >= boundsAll[1]:
        globalCenter = [0.0, 0.0, 0.0]
    else:
        globalCenter = [
            0.5 * (boundsAll[0] + boundsAll[1]),
            0.5 * (boundsAll[2] + boundsAll[3]),
            0.5 * (boundsAll[4] + boundsAll[5]),
        ]

    # 세그 display node observer 연결 (eye 끄고 켤 때 태그도 같이)
    displayNode = seg_node.GetDisplayNode()
    if displayNode:
        if slicer.tagDisplayObserverTag is not None:
            try:
                displayNode.RemoveObserver(slicer.tagDisplayObserverTag)
            except:
                pass
        slicer.tagDisplayObserverTag = displayNode.AddObserver(
            vtk.vtkCommand.ModifiedEvent, _onSegmentationDisplayModified
        )

    # closed surface는 한 번만 생성
    seg_node.CreateClosedSurfaceRepresentation()

    created_count = 0
    slicer.tagActorsBySegment = {}

    # 각 세그먼트에 태그 + 리더선 생성
    for i in range(segmentation.GetNumberOfSegments()):
        segment_id = segmentation.GetNthSegmentID(i)
        segment = segmentation.GetSegment(segment_id)
        name = segment.GetName()

        polyData = vtk.vtkPolyData()
        seg_node.GetClosedSurfaceRepresentation(segment_id, polyData)
        if polyData.GetNumberOfPoints() == 0:
            continue

        # bounds & center
        bounds = [0.0] * 6
        polyData.GetBounds(bounds)
        center = [
            0.5 * (bounds[0] + bounds[1]),
            0.5 * (bounds[2] + bounds[3]),
            0.5 * (bounds[4] + bounds[5]),
        ]

        # 🔹 global center → 세그 중심 방향
        direction = [
            center[0] - globalCenter[0],
            center[1] - globalCenter[1],
            center[2] - globalCenter[2],
        ]
        length = math.sqrt(direction[0] ** 2 + direction[1] ** 2 + direction[2] ** 2)
        if length == 0:
            direction = [0.0, 0.0, 1.0]
        else:
            direction = [d / length for d in direction]

        # 🔹 이 방향으로 "가장 멀리 나간 표면 점" 찾기
        # (= globalCenter 기준으로 해당 세그먼트에서 가장 바깥쪽 점)
        farPoint = center[:]  # fallback
        maxProj = -1e30

        numPts = polyData.GetNumberOfPoints()
        for pid in range(numPts):
            p = polyData.GetPoint(pid)
            v = [
                p[0] - globalCenter[0],
                p[1] - globalCenter[1],
                p[2] - globalCenter[2],
            ]
            proj = v[0] * direction[0] + v[1] * direction[1] + v[2] * direction[2]
            if proj > maxProj:
                maxProj = proj
                farPoint = p

        # 선 시작점: 실제 표면 위 점 (갈비뼈 끝 쪽)
        lineStart = list(farPoint)

        # 세그 크기(대각선) 기반 offset
        diag = math.sqrt(
            (bounds[1] - bounds[0]) ** 2 +
            (bounds[3] - bounds[2]) ** 2 +
            (bounds[5] - bounds[4]) ** 2
        ) or 1.0

        # 🔹 글자 위치 = lineStart 에서 다시 바깥으로
        offsetDistance = slicer.tagSize * 8 + diag * 0.2
        offsetCenter = [
            lineStart[0] + direction[0] * offsetDistance,
            lineStart[1] + direction[1] * offsetDistance,
            lineStart[2] + direction[2] * offsetDistance,
        ]

        # 텍스트 소스/액터
        textSource = vtk.vtkVectorText()
        textSource.SetText(name)

        textMapper = vtk.vtkPolyDataMapper()
        textMapper.SetInputConnection(textSource.GetOutputPort())

        textActor = vtk.vtkFollower()
        textActor.SetMapper(textMapper)
        textActor.SetPosition(offsetCenter)
        textActor.SetScale(size, size, size)

        # 텍스트 색상
        if USE_SEGMENT_COLOR:
            color = segment.GetColor()
        else:
            color = FIXED_TEXT_COLOR

        textActor.GetProperty().SetColor(color)
        textActor.GetProperty().SetLighting(False)
        textActor.GetProperty().SetAmbient(1.0)
        textActor.GetProperty().SetDiffuse(0.0)
        textActor.GetProperty().SetSpecular(0.0)
        textActor.SetCamera(renderer.GetActiveCamera())

        # 🔹 리더선 (표면 점 -> 텍스트)
        lineSource = vtk.vtkLineSource()
        lineSource.SetPoint1(lineStart)
        lineSource.SetPoint2(offsetCenter)

        lineMapper = vtk.vtkPolyDataMapper()
        lineMapper.SetInputConnection(lineSource.GetOutputPort())

        lineActor = vtk.vtkActor()
        lineActor.SetMapper(lineMapper)
        lineActor.GetProperty().SetColor(color)
        lineActor.GetProperty().SetLineWidth(1.5)
        lineActor.GetProperty().SetLighting(False)
        lineActor.GetProperty().SetAmbient(1.0)
        lineActor.GetProperty().SetDiffuse(0.0)
        lineActor.GetProperty().SetSpecular(0.0)

        # 렌더러에 추가
        renderer.AddActor(textActor)
        renderer.AddActor(lineActor)

        slicer.tagActors.append(textActor)
        slicer.tagActors.append(lineActor)
        slicer.tagActorsBySegment[segment_id] = [textActor, lineActor]
        created_count += 1

    slicer.tagVisible = True
    slicer.tagSize = size
    _updateActorsVisibilityFromSegmentation()
    slicer.updateStatus()

    print(f"✅ {created_count}개 태그 생성")
    return created_count > 0


def toggleTags():
    """태그 표시/숨김 토글."""
    if not slicer.tagActors:
        ok = slicer.createTags(slicer.tagSize)
        if not ok:
            return
    else:
        slicer.tagVisible = not slicer.tagVisible

    if slicer.tagVisible:
        print("🟢 태그 ON")
    else:
        print("🔴 태그 OFF")

    _updateActorsVisibilityFromSegmentation()
    slicer.updateStatus()


def changeSize(size):
    """태그 크기 변경 (텍스트만)."""
    slicer.tagSize = size

    # textActor는 vtkFollower, lineActor는 vtkActor
    for actor in slicer.tagActors:
        if isinstance(actor, vtk.vtkFollower):
            actor.SetScale(size, size, size)

    view, _ = _getThreeDViewAndRenderer()
    if view:
        view.forceRender()

    slicer.updateStatus()
    print(f"✅ 크기: {size}")


def updateStatus():
    """윈도우 상단 상태 레이블 업데이트."""
    if hasattr(slicer, 'tagStatusLabel'):
        if not slicer.tagActors:
            slicer.tagStatusLabel.setText("Click to create tags")
        elif slicer.tagVisible:
            slicer.tagStatusLabel.setText("🟢 Tags ON")
        else:
            slicer.tagStatusLabel.setText("🔴 Tags OFF")


# slicer 네임스페이스에 함수 등록
slicer.createTags = createTags
slicer.toggleTags = toggleTags
slicer.changeSize = changeSize
slicer.updateStatus = updateStatus

# ---------------------------------------------------------
# 플로팅 컨트롤 윈도우 UI
# ---------------------------------------------------------
window = qt.QWidget()
window.setWindowTitle("3D Tags")
window.setFixedSize(220, 100)
window.setWindowFlags(qt.Qt.WindowStaysOnTopHint)

layout = qt.QVBoxLayout(window)
layout.setContentsMargins(10, 10, 10, 10)

# 상태 레이블
slicer.tagStatusLabel = qt.QLabel("Click to create tags")
slicer.tagStatusLabel.setAlignment(qt.Qt.AlignCenter)
slicer.tagStatusLabel.setStyleSheet("""
    padding: 3px;
    background-color: #f0f0f0;
    border-radius: 3px;
    font-size: 11px;
""")
layout.addWidget(slicer.tagStatusLabel)

# ON/OFF 버튼
toggleBtn = qt.QPushButton("ON / OFF")
toggleBtn.setStyleSheet("""
    QPushButton {
        background-color: #2196F3;
        color: white;
        font-weight: bold;
        padding: 8px;
        border-radius: 4px;
    }
    QPushButton:hover {
        background-color: #1976D2;
    }
""")
toggleBtn.clicked.connect(slicer.toggleTags)
layout.addWidget(toggleBtn)

# 크기 버튼들
sizeLayout = qt.QHBoxLayout()
for label, size in [("S", 3), ("M", 5), ("L", 7), ("XL", 10)]:
    btn = qt.QPushButton(label)
    btn.setFixedWidth(40)

    def makeCallback(s):
        return lambda: slicer.changeSize(s)

    btn.clicked.connect(makeCallback(size))
    sizeLayout.addWidget(btn)

layout.addLayout(sizeLayout)

slicer.tagControlWindow = window
window.show()

# ---------------------------------------------------------
# 콘솔 단축키
# ---------------------------------------------------------
def t():
    slicer.toggleTags()


def s(size):
    slicer.changeSize(size)


print("\n✅ 3D Tags Ready!")
print("Toggle: 버튼 클릭 또는 t()")
print("Size: S/M/L/XL 버튼 또는 s(5)")
