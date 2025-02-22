import argparse
from omegaconf import OmegaConf
import torch
from torch.serialization import load
import torchvision.io as io
import torchvision.transforms as transforms
from pathlib import Path
import sys
import copy
sys.path.insert(0, './')

from lib.core.utils import seed_everything
from lib.core.trainer import Trainer
from lib.main import NeuralMaterial


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Retrieve BRDF decomposition from stationary flash image.')
    parser.add_argument('--model', type=str, required=True,
                        help='yaml config file')
    parser.add_argument('--weights1', type=str, required=True,
                        help='yaml config file')
    parser.add_argument('--weights2', type=str, required=True,
                        help='yaml config file')
    parser.add_argument('--test_image_id1', type=str, required=True,
                        help='path to stationary flash image')
    parser.add_argument('--test_image_id2', type=str, required=True,
                        help='path to stationary flash image')
    parser.add_argument('--gpu', type=bool, required=False, default=True,
                        help='use GPU / CPU')
    parser.add_argument('--h', type=int, required=False, default=384,
                        help='output height')
    parser.add_argument('--w', type=int, required=False, default=512,
                        help='output width')
    '''
    python scripts/interpolate.py --model trainings/Neuralmaterial --weights1 0280 --weights2 0281 --test_image_id1 0280 --test_image_id2 0281
    python scripts/interpolate.py --model trainings/Neuralmaterial --weights1 latest --weights2 latest --test_image_id1 0280 --test_image_id2 0281

    '''

    args = parser.parse_args()

    device = 'cpu'
    if torch.cuda.is_available() and args.gpu:
        device = 'cuda'

    # load config
    cfg = OmegaConf.load(str(Path(args.model, '.hydra', 'config.yaml')))
    seed_everything(cfg.seed)

    def load_image(image_id):

        tfm = transforms.Compose([
            transforms.ToPILImage(),
            transforms.Resize((int(cfg.data.size[0]), int(cfg.data.size[1]))),
            transforms.ToTensor()]
        )

        # load all images in folder
        image_dirs = [str(p) for p in Path('flash_images', 'test', image_id).iterdir() if p.is_file()]

        # read first image in dir, change if required
        image = tfm(io.read_image(image_dirs[0]))[None]
        image = image.to(device)

        return image

    image1 = load_image(args.test_image_id1)
    image2 = load_image(args.test_image_id2)

    # load model with weights
    def load_model(weights):
        model = NeuralMaterial(cfg.model)
        weights_path = str(Path(args.model, 'checkpoint', f'{weights}.ckpt'))
        state_dict = torch.load(weights_path)
        model.load_state_dict(state_dict)
        model.eval()
        model.to(device)

        return model

    model1 = load_model(args.weights1);
    model2 = load_model(args.weights2)

    weights1 = model1.decoder.state_dict();
    weights2 = model2.decoder.state_dict()

    z1, _, _, _ = model1.encode(image1, 'test'); 
    z2, _, _, _ = model2.encode(image2, 'test')

    n_inter = 5

    output_path = Path('outputs', 'interpolation', f'{args.test_image_id1}_{args.test_image_id2}')
    output_path.mkdir(parents=True, exist_ok=True)

    # sample noise
    x = torch.rand(1, cfg.model.w, args.h, args.w, device=device)

    for inter_idx in range(0, n_inter+2):
        a = ((inter_idx) / (n_inter + 1))
        z_inter = (1 - a) * z1 + a * z2
        
        state_dict_inter = {}

        for k in weights1.keys():            
            state_dict_inter[k] = (1 - a) * weights1[k] + a * weights2[k]

        model1.decoder.load_state_dict(state_dict_inter)

        # convert noise to brdf maps using CNN
        brdf_maps = model1.decode(z_inter, x)

        # render brdf maps using differentiable rendering
        image_out = model1.renderer(brdf_maps)

        # write outputs to disk
        io.write_png((image_out[0] * 255).byte().cpu(), str(Path(output_path,f'rendering_{inter_idx}.png')))

        for k, v in brdf_maps.items():

            if k == 'normal':
                v = (v + 1) / 2

            io.write_jpeg((v[0]* 255).byte().cpu(), str(Path(output_path, f'{k}_{inter_idx}.jpg')), quality = 100)