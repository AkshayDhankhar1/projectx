import cv2, os

resources = r'c:\Users\aksha\web d\purpll\Resources'
out_dir = os.path.join(resources, 'frames')
os.makedirs(out_dir, exist_ok=True)

for f in sorted(os.listdir(resources)):
    if f.endswith('.mp4'):
        cap = cv2.VideoCapture(os.path.join(resources, f))
        fps = cap.get(cv2.CAP_PROP_FPS)
        # Get frame from 30 seconds in
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(30 * fps))
        ret, frame = cap.read()
        if ret:
            out_name = f.replace('.mp4', '_frame30s.jpg')
            out_path = os.path.join(out_dir, out_name)
            cv2.imwrite(out_path, frame)
            print(f'Saved: {out_name}')
        cap.release()

print('Done!')
